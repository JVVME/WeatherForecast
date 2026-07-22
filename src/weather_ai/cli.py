"""Command-line interface for project configuration and ERA5 sample downloads."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from weather_ai import __version__
from weather_ai.config import ConfigError, load_config
from weather_ai.data.client import CdsApiDownloadClient, DownloadClient
from weather_ai.data.config import load_era5_download_config
from weather_ai.data.errors import DataDownloadError
from weather_ai.data.provenance import get_git_commit
from weather_ai.data.service import execute_download, plan_download
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
        return _download_era5(args.config, args.dry_run, download_client=download_client)
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
        print(f"download error: {error}", file=sys.stderr)
        return 1

    logging.getLogger(__name__).info(
        "ERA5 sample download completed",
        extra={"event": "download_completed", "target_path": record.final_file_path},
    )
    print(json.dumps(record.as_dict(), indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
