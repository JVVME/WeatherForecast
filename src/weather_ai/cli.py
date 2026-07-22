"""Command-line interface for project configuration and ERA5 data operations."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from weather_ai import __version__
from weather_ai.config import ConfigError, load_config
from weather_ai.data.client import CdsApiDownloadClient, DownloadClient
from weather_ai.data.config import load_era5_download_config
from weather_ai.data.diagnostics import (
    build_download_failure_diagnostics,
    sanitize_sensitive_text,
)
from weather_ai.data.errors import DataDownloadError, PreprocessingError
from weather_ai.data.preprocessing import execute_preprocessing, plan_preprocessing
from weather_ai.data.preprocessing_config import load_era5_preprocessing_config
from weather_ai.data.provenance import get_git_commit
from weather_ai.data.service import execute_download, plan_download
from weather_ai.data.validation import validate_era5_file
from weather_ai.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    """Build the WeatherAI argument parser."""

    parser = argparse.ArgumentParser(
        prog="weather-ai",
        description="WeatherAI research project command-line interface.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser(
        "config",
        help="validate a YAML configuration and print its resolved values",
    )
    config_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="path to the YAML configuration file",
    )

    data_parser = subparsers.add_parser("data", help="manage external weather data")
    data_subparsers = data_parser.add_subparsers(dest="data_command", required=True)
    download_parser = data_subparsers.add_parser(
        "download",
        help="download one configured ERA5 sample month",
    )
    download_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="path to an ERA5 sample YAML configuration file",
    )
    download_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the normalized request and planned paths without network or file writes",
    )
    download_parser.add_argument(
        "--verbose",
        action="store_true",
        help="show sanitized CDS failure details and the exception cause chain",
    )
    validate_parser = data_subparsers.add_parser(
        "validate",
        help="validate ERA5 NetCDF content against its download manifest",
    )
    validate_parser.add_argument(
        "--file",
        type=Path,
        required=True,
        help="path to the immutable ERA5 NetCDF file",
    )
    validate_parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="path to the M1-A JSON download manifest",
    )
    validate_parser.add_argument(
        "--output-json",
        type=Path,
        help="optional path for an atomic copy of the JSON validation report",
    )
    preprocess_parser = data_subparsers.add_parser(
        "preprocess",
        help="normalize one M1-B-passed ERA5 NetCDF into M2-A interim data",
    )
    preprocess_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="path to a strict M2-A preprocessing YAML configuration file",
    )
    preprocess_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate provenance and print the normalization plan without writes",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    download_client: DownloadClient | None = None,
) -> int:
    """Run the CLI and return a process exit code."""

    args = build_parser().parse_args(argv)
    if args.command == "config":
        return _show_config(args.config)
    if args.command == "data" and args.data_command == "download":
        return _download_era5(
            args.config,
            args.dry_run,
            args.verbose,
            download_client=download_client,
        )
    if args.command == "data" and args.data_command == "validate":
        return _validate_era5(args.file, args.manifest, args.output_json)
    if args.command == "data" and args.data_command == "preprocess":
        return _preprocess_era5(args.config, args.dry_run)
    raise AssertionError(f"unhandled command: {args.command}")


def _show_config(path: Path) -> int:
    try:
        config = load_config(path)
    except (ConfigError, OSError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2

    configure_logging(config.logging.level, config.logging.format)
    logging.getLogger(__name__).info(
        "configuration loaded",
        extra={"event": "configuration_loaded", "config_path": str(path.resolve())},
    )
    print(json.dumps(config.as_dict(), indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def _download_era5(
    path: Path,
    dry_run: bool,
    verbose: bool,
    *,
    download_client: DownloadClient | None,
) -> int:
    try:
        config = load_era5_download_config(path)
    except (ConfigError, OSError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2

    configure_logging(config.logging.level, config.logging.format)
    plan = plan_download(config)
    git_commit = get_git_commit()

    if dry_run:
        print(
            json.dumps(
                plan.as_dry_run_dict(
                    config,
                    project_version=__version__,
                    git_commit=git_commit,
                ),
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    client = download_client if download_client is not None else CdsApiDownloadClient()
    try:
        record = execute_download(
            config,
            plan,
            client,
            project_version=__version__,
            git_commit=git_commit,
        )
    except (DataDownloadError, OSError) as error:
        print(f"download error: {sanitize_sensitive_text(str(error))}", file=sys.stderr)
        if verbose:
            diagnostics = build_download_failure_diagnostics(
                error,
                dataset=plan.dataset,
                request=plan.request,
            )
            print(
                json.dumps(
                    diagnostics.as_dict(),
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        return 1

    logging.getLogger(__name__).info(
        "ERA5 sample download completed",
        extra={"event": "download_completed", "target_path": record.final_file_path},
    )
    print(json.dumps(record.as_dict(), indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def _validate_era5(file_path: Path, manifest_path: Path, output_json: Path | None) -> int:
    report = validate_era5_file(file_path, manifest_path)
    payload = report.as_dict()
    rendered = json.dumps(
        payload,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )
    print(rendered)

    if output_json is not None:
        try:
            _write_json_report(output_json, rendered, protected_paths=(file_path, manifest_path))
        except OSError as error:
            print(f"validation report output error: {error}", file=sys.stderr)
            return 2
    return 1 if report.error_count else 0


def _preprocess_era5(path: Path, dry_run: bool) -> int:
    try:
        config = load_era5_preprocessing_config(path)
    except (ConfigError, OSError) as error:
        print(f"configuration error: {sanitize_sensitive_text(str(error))}", file=sys.stderr)
        return 2

    try:
        plan = plan_preprocessing(config)
        if dry_run:
            print(
                json.dumps(
                    plan.as_dry_run_dict(config),
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
            )
            return 0

        manifest = execute_preprocessing(
            config,
            plan,
            software_version=__version__,
            git_commit=get_git_commit(),
        )
    except (PreprocessingError, OSError, TypeError, ValueError) as error:
        print(f"preprocessing error: {sanitize_sensitive_text(str(error))}", file=sys.stderr)
        return 1

    logging.getLogger(__name__).info(
        "ERA5 M2-A preprocessing completed",
        extra={
            "event": "preprocessing_completed",
            "source_path": manifest.source_file,
            "target_path": manifest.output_file,
            "warning_count": len(manifest.source_validation_warnings),
        },
    )
    print(
        json.dumps(
            manifest.as_dict(),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


def _write_json_report(
    output_path: Path,
    rendered: str,
    *,
    protected_paths: tuple[Path, Path],
) -> None:
    resolved_output = output_path.expanduser().resolve()
    protected = {path.expanduser().resolve() for path in protected_paths}
    if resolved_output in protected:
        raise OSError("--output-json must not overwrite the NetCDF file or manifest")

    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved_output.with_suffix(resolved_output.suffix + ".tmp")
    if temporary.exists():
        raise OSError(f"temporary report path already exists: {temporary}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as file_handle:
            file_handle.write(rendered)
            file_handle.write("\n")
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary, resolved_output)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
