"""Strict configuration for the M2-A ERA5 normalization pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from weather_ai.config import ConfigError
from weather_ai.data.variables import SUPPORTED_PREPROCESSING_VARIABLES

PreprocessingOutputFormat = Literal["netcdf4"]
PreprocessingDType = Literal["float32", "float64"]
CoordinateSortOrder = Literal["ascending"]


@dataclass(frozen=True, slots=True)
class PreprocessingSourceConfig:
    """Immutable source and its M1 provenance documents."""

    file: Path
    download_manifest: Path
    validation_report: Path


@dataclass(frozen=True, slots=True)
class PreprocessingOutputConfig:
    """One NetCDF4/HDF5 output destination."""

    directory: Path
    format: PreprocessingOutputFormat
    allow_overwrite: bool = False


@dataclass(frozen=True, slots=True)
class Era5PreprocessingConfig:
    """Validated M2-A inputs and normalization policy."""

    source: PreprocessingSourceConfig
    output: PreprocessingOutputConfig
    expected_variables: tuple[str, ...]
    dtype: PreprocessingDType
    coordinate_sort: CoordinateSortOrder


def load_era5_preprocessing_config(path: Path) -> Era5PreprocessingConfig:
    """Load a strict M2-A YAML file and require all source evidence to exist."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in {path}: {error}") from error

    root = _mapping(raw, "configuration root")
    _reject_unknown(
        root,
        {"source", "output", "expected_variables", "dtype", "coordinate_sort"},
        "configuration root",
    )

    source_raw = _mapping(_required(root, "source", "configuration root"), "source")
    _reject_unknown(
        source_raw,
        {"file", "download_manifest", "validation_report"},
        "source",
    )
    source = PreprocessingSourceConfig(
        file=_existing_file(_required(source_raw, "file", "source"), "source.file"),
        download_manifest=_existing_file(
            _required(source_raw, "download_manifest", "source"),
            "source.download_manifest",
        ),
        validation_report=_existing_file(
            _required(source_raw, "validation_report", "source"),
            "source.validation_report",
        ),
    )
    if len({source.file, source.download_manifest, source.validation_report}) != 3:
        raise ConfigError("source file, download manifest, and validation report must differ")

    output_raw = _mapping(_required(root, "output", "configuration root"), "output")
    _reject_unknown(output_raw, {"directory", "format", "allow_overwrite"}, "output")
    output_format = _string(_required(output_raw, "format", "output"), "output.format")
    if output_format != "netcdf4":
        raise ConfigError("output.format must be 'netcdf4'; M2-A supports one backend")
    output_directory = _path(
        _required(output_raw, "directory", "output"), "output.directory"
    )
    allow_overwrite = _boolean(output_raw.get("allow_overwrite", False), "output.allow_overwrite")
    if output_directory == source.file.parent:
        raise ConfigError("output.directory must differ from the immutable raw-file directory")

    variables = _string_tuple(
        _required(root, "expected_variables", "configuration root"),
        "expected_variables",
    )
    supported = set(SUPPORTED_PREPROCESSING_VARIABLES)
    unknown = sorted(set(variables) - supported)
    missing = sorted(supported - set(variables))
    if unknown:
        raise ConfigError(f"unknown preprocessing variable(s): {', '.join(unknown)}")
    if missing:
        raise ConfigError(f"missing required preprocessing variable(s): {', '.join(missing)}")

    dtype = _string(_required(root, "dtype", "configuration root"), "dtype")
    if dtype not in {"float32", "float64"}:
        raise ConfigError("dtype must be 'float32' or 'float64'")
    coordinate_sort = _string(
        _required(root, "coordinate_sort", "configuration root"),
        "coordinate_sort",
    )
    if coordinate_sort != "ascending":
        raise ConfigError("coordinate_sort must be 'ascending'")

    return Era5PreprocessingConfig(
        source=source,
        output=PreprocessingOutputConfig(
            directory=output_directory,
            format=cast(PreprocessingOutputFormat, output_format),
            allow_overwrite=allow_overwrite,
        ),
        expected_variables=variables,
        dtype=cast(PreprocessingDType, dtype),
        coordinate_sort=cast(CoordinateSortOrder, coordinate_sort),
    )


def _mapping(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{location} must be a mapping with string keys")
    return cast(dict[str, Any], value)


def _required(mapping: dict[str, Any], key: str, location: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"missing required key: {location}.{key}")
    return mapping[key]


def _reject_unknown(mapping: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(mapping.keys() - allowed)
    if unknown:
        raise ConfigError(f"unknown key(s) in {location}: {', '.join(unknown)}")


def _string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} must be a non-empty string")
    return value.strip()


def _string_tuple(value: object, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{location} must be a non-empty list")
    result = tuple(_string(item, f"{location}[]") for item in value)
    if len(set(result)) != len(result):
        raise ConfigError(f"{location} must not contain duplicates")
    return result


def _boolean(value: object, location: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{location} must be a boolean")
    return value


def _path(value: object, location: str) -> Path:
    configured = Path(_string(value, location)).expanduser()
    return configured.resolve()


def _existing_file(value: object, location: str) -> Path:
    resolved = _path(value, location)
    if not resolved.is_file():
        raise ConfigError(f"{location} does not exist or is not a file: {resolved}")
    return resolved
