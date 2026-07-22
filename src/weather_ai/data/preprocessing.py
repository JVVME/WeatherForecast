"""Safe orchestration of the M2-A ERA5 normalization pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from weather_ai.data.config import ERA5_SINGLE_LEVELS_DATASET
from weather_ai.data.errors import (
    PreprocessingError,
    PreprocessingPreconditionError,
)
from weather_ai.data.files import sha256_file
from weather_ai.data.inspection import open_era5_dataset
from weather_ai.data.normalization import normalize_era5_dataset, validate_normalized_dataset
from weather_ai.data.preprocessing_config import Era5PreprocessingConfig
from weather_ai.data.preprocessing_manifest import write_preprocessing_manifest
from weather_ai.data.preprocessing_schemas import (
    PreprocessingManifest,
    UnitConversionRecord,
)
from weather_ai.data.variables import get_variable_spec

PREPROCESSING_SCHEMA_VERSION = "1.0"
PREPROCESSING_VERSION = "m2a-v1"
OUTPUT_FORMAT_DESCRIPTION = "NetCDF4/HDF5 (h5netcdf)"
_REGION_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    """Cross-checked M1-A, M1-B, and current-file evidence."""

    file_size: int
    sha256: str
    dataset: str
    year: int
    month: int
    region: str
    variables: tuple[str, ...]
    validation_status: str
    validation_warnings: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class PreprocessingPaths:
    """Stable final and same-directory temporary paths."""

    output: Path
    temporary_output: Path
    manifest: Path
    temporary_manifest: Path


@dataclass(frozen=True, slots=True)
class PreprocessingPlan:
    """Fully resolved dry-run plan that has passed M1 provenance checks."""

    source: SourceEvidence
    paths: PreprocessingPaths
    config_hash: str
    expected_variables: tuple[str, ...]

    def as_dry_run_dict(self, config: Era5PreprocessingConfig) -> dict[str, object]:
        """Return the required no-write transformation plan."""

        conversions = _planned_conversions(self.expected_variables)
        return {
            "status": "dry_run",
            "source_file": _display_path(config.source.file),
            "source_download_manifest": _display_path(config.source.download_manifest),
            "source_validation_report": _display_path(config.source.validation_report),
            "source_validation_status": self.source.validation_status,
            "source_validation_warning_count": len(self.source.validation_warnings),
            "source_file_sha256": self.source.sha256,
            "target_file": _display_path(self.paths.output),
            "target_manifest": _display_path(self.paths.manifest),
            "preprocessing_version": PREPROCESSING_VERSION,
            "config_hash": self.config_hash,
            "variable_mapping": {
                conversion.input_name: conversion.output_name for conversion in conversions
            },
            "unit_conversions": [conversion.as_dict() for conversion in conversions],
            "coordinate_normalization_plan": {
                "rename_to": ["time", "latitude", "longitude"],
                "time": "preserve UTC; require strictly increasing; do not fill gaps",
                "latitude": "sort ascending with data reindexing if decreasing",
                "longitude": "sort ascending; preserve the source longitude domain",
                "interpolation": "none",
            },
            "dimension_order": ["time", "latitude", "longitude"],
            "dtype": config.dtype,
            "output_format": OUTPUT_FORMAT_DESCRIPTION,
            "writes_performed": False,
        }


def plan_preprocessing(config: Era5PreprocessingConfig) -> PreprocessingPlan:
    """Validate M1 evidence and derive one deterministic output name."""

    source = _load_source_evidence(config)
    hash_payload = {
        "preprocessing_version": PREPROCESSING_VERSION,
        "source_sha256": source.sha256,
        "dataset": source.dataset,
        "year": source.year,
        "month": source.month,
        "region": source.region,
        "variables": list(config.expected_variables),
        "dtype": config.dtype,
        "coordinate_sort": config.coordinate_sort,
        "format": config.output.format,
    }
    serialized = json.dumps(hash_payload, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
    filename = (
        f"era5_single_levels_{source.year:04d}_{source.month:02d}_{source.region}_"
        f"{PREPROCESSING_VERSION}_{config_hash}_normalized.nc"
    )
    output = config.output.directory / filename
    manifest = output.with_suffix(".manifest.json")
    return PreprocessingPlan(
        source=source,
        paths=PreprocessingPaths(
            output=output,
            temporary_output=output.with_suffix(output.suffix + ".tmp"),
            manifest=manifest,
            temporary_manifest=manifest.with_suffix(manifest.suffix + ".tmp"),
        ),
        config_hash=config_hash,
        expected_variables=config.expected_variables,
    )


def execute_preprocessing(
    config: Era5PreprocessingConfig,
    plan: PreprocessingPlan,
    *,
    software_version: str,
    git_commit: str | None,
) -> PreprocessingManifest:
    """Write, validate, and atomically publish one normalized NetCDF and manifest."""

    _ensure_destinations_available(config, plan.paths)
    plan.paths.output.parent.mkdir(parents=True, exist_ok=True)
    _ensure_destinations_available(config, plan.paths)

    current_source_hash = sha256_file(config.source.file)
    if current_source_hash != plan.source.sha256:
        raise PreprocessingPreconditionError(
            "source SHA-256 changed after planning and before preprocessing"
        )

    normalization = None
    try:
        with open_era5_dataset(config.source.file) as source_dataset:
            normalization = normalize_era5_dataset(
                source_dataset,
                config.expected_variables,
                dtype=config.dtype,
            )
            normalization.dataset.attrs.update(
                {
                    "preprocessing_version": PREPROCESSING_VERSION,
                    "source_dataset": plan.source.dataset,
                    "source_file_sha256": plan.source.sha256,
                    "source_year": plan.source.year,
                    "source_month": plan.source.month,
                    "source_region": plan.source.region,
                }
            )
            encoding = {
                name: {"dtype": config.dtype, "compression": "gzip", "compression_opts": 4}
                for name in normalization.dataset.data_vars
            }
            normalization.dataset.to_netcdf(
                plan.paths.temporary_output,
                engine="h5netcdf",
                format="NETCDF4",
                mode="w",
                encoding=encoding,
            )
    except Exception:
        plan.paths.temporary_output.unlink(missing_ok=True)
        raise
    finally:
        if normalization is not None:
            normalization.dataset.close()

    try:
        with open_era5_dataset(plan.paths.temporary_output) as written_dataset:
            variable_summaries = validate_normalized_dataset(
                written_dataset,
                config.expected_variables,
                dtype=config.dtype,
            )

        final_source_hash = sha256_file(config.source.file)
        if final_source_hash != plan.source.sha256:
            raise PreprocessingPreconditionError(
                "source SHA-256 changed while preprocessing was running"
            )

        output_size = plan.paths.temporary_output.stat().st_size
        output_hash = sha256_file(plan.paths.temporary_output)
        manifest = PreprocessingManifest(
            schema_version=PREPROCESSING_SCHEMA_VERSION,
            preprocessing_version=PREPROCESSING_VERSION,
            created_at_utc=datetime.now(UTC).isoformat(),
            source_file=_display_path(config.source.file),
            source_file_size=plan.source.file_size,
            source_file_sha256=plan.source.sha256,
            source_download_manifest=_display_path(config.source.download_manifest),
            source_validation_report=_display_path(config.source.validation_report),
            source_validation_status=plan.source.validation_status,
            source_validation_warnings=plan.source.validation_warnings,
            source_dataset=plan.source.dataset,
            source_year=plan.source.year,
            source_month=plan.source.month,
            source_region=plan.source.region,
            input_variables=config.expected_variables,
            output_variables=tuple(summary.name for summary in variable_summaries),
            unit_conversions=normalization.conversions,
            coordinate_normalization=normalization.coordinates,
            dimension_order=("time", "latitude", "longitude"),
            output_file=_display_path(plan.paths.output),
            output_file_size=output_size,
            output_file_sha256=output_hash,
            output_format=OUTPUT_FORMAT_DESCRIPTION,
            software_version=software_version,
            git_commit=git_commit,
            status="success",
            variables=variable_summaries,
        )

        _publish_output_and_manifest(
            config,
            plan.paths,
            manifest,
            expected_output_hash=output_hash,
        )
        return manifest
    except Exception:
        plan.paths.temporary_output.unlink(missing_ok=True)
        plan.paths.temporary_manifest.unlink(missing_ok=True)
        raise


def _ensure_destinations_available(
    config: Era5PreprocessingConfig, paths: PreprocessingPaths
) -> None:
    backup_output, backup_manifest = _backup_paths(paths)
    for temporary in (
        paths.temporary_output,
        paths.temporary_manifest,
        backup_output,
        backup_manifest,
    ):
        if temporary.exists():
            raise PreprocessingError(f"refusing to overwrite temporary file: {temporary}")
    if not config.output.allow_overwrite:
        for final in (paths.output, paths.manifest):
            if final.exists():
                raise PreprocessingError(f"refusing to overwrite existing output: {final}")


def _publish_output_and_manifest(
    config: Era5PreprocessingConfig,
    paths: PreprocessingPaths,
    manifest: PreprocessingManifest,
    *,
    expected_output_hash: str,
) -> None:
    """Publish both artifacts, restoring prior versions if an allowed overwrite fails."""

    backup_output, backup_manifest = _backup_paths(paths)
    backed_up_output = False
    backed_up_manifest = False
    try:
        if config.output.allow_overwrite and paths.output.exists():
            os.replace(paths.output, backup_output)
            backed_up_output = True
        if config.output.allow_overwrite and paths.manifest.exists():
            os.replace(paths.manifest, backup_manifest)
            backed_up_manifest = True

        if paths.output.exists() and not config.output.allow_overwrite:
            raise PreprocessingError(f"refusing to overwrite output file: {paths.output}")
        os.replace(paths.temporary_output, paths.output)
        if sha256_file(paths.output) != expected_output_hash:
            raise PreprocessingError(
                "published output SHA-256 differs from the validated temporary file"
            )
        write_preprocessing_manifest(
            paths.manifest,
            manifest,
            allow_overwrite=config.output.allow_overwrite,
        )
    except Exception:
        paths.output.unlink(missing_ok=True)
        if backed_up_output:
            os.replace(backup_output, paths.output)
        if backed_up_manifest:
            paths.manifest.unlink(missing_ok=True)
            os.replace(backup_manifest, paths.manifest)
        raise
    else:
        backup_output.unlink(missing_ok=True)
        backup_manifest.unlink(missing_ok=True)


def _backup_paths(paths: PreprocessingPaths) -> tuple[Path, Path]:
    return (
        paths.output.with_suffix(paths.output.suffix + ".bak"),
        paths.manifest.with_suffix(paths.manifest.suffix + ".bak"),
    )


def _load_source_evidence(config: Era5PreprocessingConfig) -> SourceEvidence:
    source_size = config.source.file.stat().st_size
    source_hash = sha256_file(config.source.file)
    download_record = _matching_download_record(
        config.source.download_manifest,
        config.source.file,
    )

    dataset = _required_string(download_record, "dataset", "download manifest")
    if dataset != ERA5_SINGLE_LEVELS_DATASET:
        raise PreprocessingPreconditionError(f"unsupported source dataset: {dataset!r}")
    if download_record.get("download_status") != "success":
        raise PreprocessingPreconditionError("download manifest status must be 'success'")
    manifest_size = _positive_integer(
        download_record.get("file_size_bytes"), "download manifest file_size_bytes"
    )
    manifest_hash = _sha256(download_record.get("sha256"), "download manifest sha256")
    if manifest_size != source_size:
        raise PreprocessingPreconditionError(
            "download manifest file size does not match the current source file"
        )
    if manifest_hash != source_hash:
        raise PreprocessingPreconditionError(
            "download manifest SHA-256 does not match the current source file"
        )

    variables = _string_tuple(download_record.get("variables"), "download manifest variables")
    if set(variables) != set(config.expected_variables) or len(variables) != len(
        config.expected_variables
    ):
        raise PreprocessingPreconditionError(
            "download manifest variables do not match expected_variables"
        )
    request = _mapping(download_record.get("request_parameters"), "request_parameters")
    year = _single_integer(request.get("year"), "request_parameters.year")
    month = _single_integer(request.get("month"), "request_parameters.month")
    if not 1940 <= year <= 2100 or not 1 <= month <= 12:
        raise PreprocessingPreconditionError("download manifest year or month is invalid")
    area = _mapping(download_record.get("area"), "download manifest area")
    region = _required_string(area, "id", "download manifest area")
    if not _REGION_PATTERN.fullmatch(region):
        raise PreprocessingPreconditionError("download manifest region id is not filename-safe")

    validation_status, validation_warnings = _validate_m1_report(
        config,
        source_size=source_size,
        source_hash=source_hash,
    )
    return SourceEvidence(
        file_size=source_size,
        sha256=source_hash,
        dataset=dataset,
        year=year,
        month=month,
        region=region,
        variables=variables,
        validation_status=validation_status,
        validation_warnings=validation_warnings,
    )


def _matching_download_record(manifest_path: Path, source_file: Path) -> dict[str, object]:
    raw = _read_json(manifest_path, "download manifest")
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise PreprocessingPreconditionError("download manifest must be an array of objects")
    records = [cast(dict[str, object], item) for item in raw]
    matching = [
        record
        for record in records
        if _recorded_path_matches(record.get("final_file_path"), source_file, manifest_path)
    ]
    if len(matching) != 1:
        raise PreprocessingPreconditionError(
            "download manifest must contain exactly one record for source_file; "
            f"found {len(matching)}"
        )
    return matching[0]


def _validate_m1_report(
    config: Era5PreprocessingConfig,
    *,
    source_size: int,
    source_hash: str,
) -> tuple[str, tuple[dict[str, object], ...]]:
    raw = _read_json(config.source.validation_report, "M1-B validation report")
    report = _mapping(raw, "M1-B validation report")
    status = _required_string(report, "status", "M1-B validation report")
    if status == "failed":
        raise PreprocessingPreconditionError("M1-B validation report status is 'failed'")
    if status not in {"passed", "passed_with_warnings"}:
        raise PreprocessingPreconditionError(f"unsupported M1-B validation status: {status!r}")
    if report.get("error_count") != 0:
        raise PreprocessingPreconditionError("M1-B validation report contains errors")
    if not _recorded_path_matches(
        report.get("file_path"), config.source.file, config.source.validation_report
    ):
        raise PreprocessingPreconditionError(
            "M1-B validation report file_path does not match source.file"
        )
    if not _recorded_path_matches(
        report.get("manifest_path"),
        config.source.download_manifest,
        config.source.validation_report,
    ):
        raise PreprocessingPreconditionError(
            "M1-B validation report manifest_path does not match source.download_manifest"
        )

    report_size = _positive_integer(
        report.get("file_size_bytes"), "M1-B validation report file_size_bytes"
    )
    report_hash = _sha256(report.get("file_sha256"), "M1-B validation report file_sha256")
    final_report_hash = _sha256(
        report.get("file_sha256_after_validation"),
        "M1-B validation report file_sha256_after_validation",
    )
    if report_size != source_size:
        raise PreprocessingPreconditionError(
            "M1-B validation report file size does not match the current source file"
        )
    if report_hash != source_hash or final_report_hash != source_hash:
        raise PreprocessingPreconditionError(
            "M1-B validation report SHA-256 does not match the current source file"
        )

    issues = report.get("issues")
    if not isinstance(issues, list) or not all(isinstance(issue, dict) for issue in issues):
        raise PreprocessingPreconditionError("M1-B validation report issues must be an array")
    warnings = tuple(
        cast(dict[str, object], issue)
        for issue in issues
        if issue.get("severity") == "warning"
    )
    warning_count = report.get("warning_count")
    if not isinstance(warning_count, int) or isinstance(warning_count, bool):
        raise PreprocessingPreconditionError("M1-B warning_count must be an integer")
    if warning_count != len(warnings):
        raise PreprocessingPreconditionError("M1-B warning_count is inconsistent with issues")
    if status == "passed" and warnings:
        raise PreprocessingPreconditionError("M1-B passed status cannot contain warnings")
    if status == "passed_with_warnings" and not warnings:
        raise PreprocessingPreconditionError(
            "M1-B passed_with_warnings status must retain at least one warning"
        )
    return status, warnings


def _planned_conversions(variables: tuple[str, ...]) -> tuple[UnitConversionRecord, ...]:
    conversions: list[UnitConversionRecord] = []
    for requested_name in variables:
        spec = get_variable_spec(requested_name)
        if spec is None:
            raise PreprocessingPreconditionError(
                f"unknown ERA5 requested variable: {requested_name!r}"
            )
        conversions.append(
            UnitConversionRecord(
                input_name=spec.internal_name,
                output_name=spec.output_name,
                input_units=spec.canonical_units,
                output_units=spec.output_units,
                operation=spec.conversion_expression,
            )
        )
    return tuple(conversions)


def _read_json(path: Path, description: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PreprocessingPreconditionError(
            f"could not read valid {description} JSON: {path}"
        ) from error


def _recorded_path_matches(value: object, expected: Path, evidence_path: Path) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    recorded = Path(value).expanduser()
    candidates = (
        [recorded.resolve()]
        if recorded.is_absolute()
        else [(Path.cwd() / recorded).resolve(), (evidence_path.parent / recorded).resolve()]
    )
    return expected.resolve() in candidates


def _mapping(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PreprocessingPreconditionError(f"{description} must be an object")
    return cast(dict[str, object], value)


def _required_string(mapping: dict[str, object], key: str, description: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PreprocessingPreconditionError(f"{description}.{key} must be a non-empty string")
    return value.strip()


def _positive_integer(value: object, description: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PreprocessingPreconditionError(f"{description} must be a positive integer")
    return value


def _sha256(value: object, description: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise PreprocessingPreconditionError(f"{description} must be a SHA-256 hex digest")
    normalized = value.lower()
    if any(character not in "0123456789abcdef" for character in normalized):
        raise PreprocessingPreconditionError(f"{description} must be a SHA-256 hex digest")
    return normalized


def _string_tuple(value: object, description: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise PreprocessingPreconditionError(f"{description} must be a non-empty string array")
    result = tuple(cast(str, item).strip() for item in value)
    if len(set(result)) != len(result):
        raise PreprocessingPreconditionError(f"{description} must not contain duplicates")
    return result


def _single_integer(value: object, description: str) -> int:
    if not isinstance(value, list) or len(value) != 1:
        raise PreprocessingPreconditionError(f"{description} must contain one integer")
    item = value[0]
    if isinstance(item, bool):
        raise PreprocessingPreconditionError(f"{description} must contain one integer")
    if isinstance(item, int):
        return item
    if isinstance(item, str) and item.isascii() and item.isdigit():
        return int(item)
    raise PreprocessingPreconditionError(f"{description} must contain one integer")


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)
