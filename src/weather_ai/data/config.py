"""Strict configuration model for one-month ERA5 sample downloads."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from weather_ai.config import ConfigError, LoggingConfig

SampleScope = Literal["sample"]
OutputFormat = Literal["netcdf"]

ERA5_SINGLE_LEVELS_DATASET = "reanalysis-era5-single-levels"
MAX_SAMPLE_AREA_SPAN_DEGREES = 5.0
MAX_SAMPLE_VARIABLES = 10
_AREA_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class Era5Area:
    """CDS bounding box in north, west, south, east order."""

    identifier: str
    north: float
    west: float
    south: float
    east: float

    def as_cds_list(self) -> list[float]:
        """Return the coordinate order required by the CDS API."""

        return [self.north, self.west, self.south, self.east]

    def as_dict(self) -> dict[str, object]:
        """Return stable manifest metadata."""

        return {
            "id": self.identifier,
            "north": self.north,
            "west": self.west,
            "south": self.south,
            "east": self.east,
        }


@dataclass(frozen=True, slots=True)
class Era5OutputConfig:
    """Filesystem destinations and requested CDS output format."""

    format: OutputFormat
    directory: Path
    manifest: Path


@dataclass(frozen=True, slots=True)
class Era5DownloadConfig:
    """Validated configuration for exactly one small ERA5 calendar month."""

    scope: SampleScope
    dataset: str
    year: int
    month: int
    area: Era5Area
    variables: tuple[str, ...]
    output: Era5OutputConfig
    logging: LoggingConfig


def load_era5_download_config(path: Path) -> Era5DownloadConfig:
    """Load a strict one-month ERA5 sample configuration from YAML.

    Paths are retained as configured and are resolved against the process working directory when a
    download plan is built. Credential fields are deliberately not part of this schema; ``cdsapi``
    reads credentials from the user's standard local configuration.
    """

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in {path}: {error}") from error

    root = _mapping(raw, "configuration root")
    _reject_unknown(
        root,
        {"scope", "dataset", "year", "month", "area", "variables", "output", "logging"},
        "configuration root",
    )

    scope = _non_empty_string(_required(root, "scope", "configuration root"), "scope")
    if scope != "sample":
        raise ConfigError("scope must be 'sample'; multi-month or bulk downloads are not supported")

    dataset = _non_empty_string(
        _required(root, "dataset", "configuration root"), "dataset"
    )
    if dataset != ERA5_SINGLE_LEVELS_DATASET:
        raise ConfigError(
            f"dataset must be '{ERA5_SINGLE_LEVELS_DATASET}' for the M1-A ERA5 request builder"
        )

    year = _integer(_required(root, "year", "configuration root"), "year")
    if not 1940 <= year <= 2100:
        raise ConfigError("year must be between 1940 and 2100")

    month = _integer(_required(root, "month", "configuration root"), "month")
    if not 1 <= month <= 12:
        raise ConfigError("month must be between 1 and 12")

    area = _parse_area(_required(root, "area", "configuration root"))
    variables = _parse_variables(_required(root, "variables", "configuration root"))
    output = _parse_output(_required(root, "output", "configuration root"))
    logging_config = _parse_logging(root.get("logging", {}))

    return Era5DownloadConfig(
        scope=cast(SampleScope, scope),
        dataset=dataset,
        year=year,
        month=month,
        area=area,
        variables=variables,
        output=output,
        logging=logging_config,
    )


def _parse_area(value: object) -> Era5Area:
    raw = _mapping(value, "area")
    _reject_unknown(raw, {"id", "north", "west", "south", "east"}, "area")
    identifier = _non_empty_string(_required(raw, "id", "area"), "area.id")
    if _AREA_ID_PATTERN.fullmatch(identifier) is None:
        raise ConfigError(
            "area.id must contain lowercase letters or digits separated by '-' or '_'"
        )

    north = _finite_number(_required(raw, "north", "area"), "area.north")
    west = _finite_number(_required(raw, "west", "area"), "area.west")
    south = _finite_number(_required(raw, "south", "area"), "area.south")
    east = _finite_number(_required(raw, "east", "area"), "area.east")

    if not -90.0 <= south <= 90.0 or not -90.0 <= north <= 90.0:
        raise ConfigError("area north and south must be within [-90, 90]")
    if not -180.0 <= west <= 180.0 or not -180.0 <= east <= 180.0:
        raise ConfigError("area west and east must be within [-180, 180]")
    if north <= south:
        raise ConfigError("area.north must be greater than area.south")
    if east <= west:
        raise ConfigError("area.east must be greater than area.west")
    if north - south > MAX_SAMPLE_AREA_SPAN_DEGREES:
        raise ConfigError(
            f"sample area latitude span must not exceed {MAX_SAMPLE_AREA_SPAN_DEGREES:g} degrees"
        )
    if east - west > MAX_SAMPLE_AREA_SPAN_DEGREES:
        raise ConfigError(
            f"sample area longitude span must not exceed {MAX_SAMPLE_AREA_SPAN_DEGREES:g} degrees"
        )

    return Era5Area(
        identifier=identifier,
        north=north,
        west=west,
        south=south,
        east=east,
    )


def _parse_variables(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError("variables must be a list")
    variables = tuple(_non_empty_string(item, "variables item") for item in value)
    if not variables:
        raise ConfigError("variables must not be empty")
    if len(variables) > MAX_SAMPLE_VARIABLES:
        raise ConfigError(f"sample variables must contain at most {MAX_SAMPLE_VARIABLES} items")
    if len(set(variables)) != len(variables):
        raise ConfigError("variables must not contain duplicates")
    if "total_precipitation" in variables:
        raise ConfigError(
            "total_precipitation is outside M1-A because its accumulation semantics require "
            "separate validation"
        )
    return tuple(sorted(variables))


def _parse_output(value: object) -> Era5OutputConfig:
    raw = _mapping(value, "output")
    _reject_unknown(raw, {"format", "directory", "manifest"}, "output")
    output_format = _non_empty_string(_required(raw, "format", "output"), "output.format")
    if output_format != "netcdf":
        raise ConfigError("output.format must be 'netcdf' for M1-A")
    directory = Path(
        _non_empty_string(_required(raw, "directory", "output"), "output.directory")
    )
    manifest = Path(_non_empty_string(_required(raw, "manifest", "output"), "output.manifest"))
    return Era5OutputConfig(
        format=cast(OutputFormat, output_format),
        directory=directory,
        manifest=manifest,
    )


def _parse_logging(value: object) -> LoggingConfig:
    raw = _mapping(value, "logging")
    _reject_unknown(raw, {"level", "format"}, "logging")
    level = _non_empty_string(raw.get("level", "INFO"), "logging.level").upper()
    if level not in _LOG_LEVELS:
        allowed = ", ".join(sorted(_LOG_LEVELS))
        raise ConfigError(f"logging.level must be one of: {allowed}")
    log_format = _non_empty_string(raw.get("format", "json"), "logging.format")
    if log_format not in {"json", "text"}:
        raise ConfigError("logging.format must be 'json' or 'text'")
    return LoggingConfig(level=level, format=cast(Literal["json", "text"], log_format))


def _mapping(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{location} must be a mapping with string keys")
    return cast(dict[str, Any], value)


def _required(mapping: dict[str, Any], key: str, location: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"missing required key: {location}.{key}")
    return mapping[key]


def _non_empty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} must be a non-empty string")
    return value.strip()


def _integer(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{location} must be an integer")
    return value


def _finite_number(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{location} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError(f"{location} must be finite")
    return result


def _reject_unknown(mapping: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(mapping.keys() - allowed)
    if unknown:
        raise ConfigError(f"unknown key(s) in {location}: {', '.join(unknown)}")
