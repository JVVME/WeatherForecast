"""ERA5 NetCDF content and download-manifest validation rules."""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
import xarray as xr
from numpy.typing import NDArray

from weather_ai.data.config import ERA5_SINGLE_LEVELS_DATASET
from weather_ai.data.files import sha256_file
from weather_ai.data.inspection import (
    COORDINATE_ALIASES,
    NETCDF_ENGINE,
    compute_numeric_statistics,
    coordinate_candidates,
    coordinate_direction,
    is_regular_axis,
    open_era5_dataset,
)
from weather_ai.data.schemas import (
    DatasetSummary,
    ValidationIssue,
    ValidationReport,
    VariableSummary,
    validation_status,
)
from weather_ai.data.variables import Era5VariableSpec, get_variable_spec, normalize_units

COORDINATE_TOLERANCE_DEGREES = 1e-6
EXPECTED_TIME_STEP = np.timedelta64(1, "h")


@dataclass(frozen=True, slots=True)
class ManifestArea:
    """Requested bounding box in north, west, south, east order."""

    north: float
    west: float
    south: float
    east: float


@dataclass(frozen=True, slots=True)
class ManifestRequest:
    """Validated manifest fields needed for content comparison."""

    dataset: str
    year: int
    month: int
    variables: tuple[str, ...]
    area: ManifestArea
    output_format: str
    file_size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class TimeInspection:
    """Observed time-axis details used in the public summary."""

    dimension: str | None
    start: str | None
    end: str | None
    count: int | None
    resolution: str | None


@dataclass(frozen=True, slots=True)
class SpatialInspection:
    """Observed one-dimensional spatial-axis details."""

    dimension: str | None
    minimum: float | None
    maximum: float | None
    direction: str | None


def validate_era5_file(file_path: Path, manifest_path: Path) -> ValidationReport:
    """Validate one ERA5 NetCDF against one M1-A JSON manifest.

    The input file is opened read-only through xarray's explicit ``netcdf4`` backend.
    Data variables are reduced in bounded chunks and the original file hash is checked
    again after the dataset has been closed.
    """

    resolved_file = file_path.expanduser().resolve()
    resolved_manifest = manifest_path.expanduser().resolve()
    issues: list[ValidationIssue] = []

    record = _load_manifest_record(resolved_manifest, resolved_file, issues)
    if record is None:
        return _report(resolved_file, resolved_manifest, issues, dataset=None)

    request = _parse_manifest_request(record, issues)
    _validate_manifest_file_path(record, resolved_file, resolved_manifest, issues)

    if not resolved_file.is_file():
        _error(issues, "file_missing", f"NetCDF file does not exist: {resolved_file}")
        return _report(resolved_file, resolved_manifest, issues, dataset=None)

    file_size = resolved_file.stat().st_size
    initial_hash = sha256_file(resolved_file)
    if request is not None:
        if file_size != request.file_size_bytes:
            _error(
                issues,
                "manifest_size_mismatch",
                f"manifest size {request.file_size_bytes} does not match file size {file_size}",
            )
        if initial_hash.lower() != request.sha256.lower():
            _error(
                issues,
                "manifest_sha256_mismatch",
                "computed SHA-256 does not match the manifest",
            )

    dataset_summary: DatasetSummary | None = None
    try:
        with open_era5_dataset(resolved_file) as dataset:
            dataset_summary = _validate_dataset(dataset, request, issues)
    except (ImportError, OSError, ValueError) as error:
        _error(
            issues,
            "netcdf_open_failed",
            f"could not open NetCDF with the {NETCDF_ENGINE!r} backend: {error}",
        )

    final_hash = sha256_file(resolved_file)
    if final_hash != initial_hash:
        _error(
            issues,
            "file_modified_during_validation",
            "the NetCDF SHA-256 changed while validation was running",
        )

    return _report(
        resolved_file,
        resolved_manifest,
        issues,
        dataset=dataset_summary,
        file_size_bytes=file_size,
        file_sha256=initial_hash,
        file_sha256_after_validation=final_hash,
    )


def _load_manifest_record(
    manifest_path: Path,
    file_path: Path,
    issues: list[ValidationIssue],
) -> dict[str, object] | None:
    if not manifest_path.is_file():
        _error(issues, "manifest_missing", f"manifest does not exist: {manifest_path}")
        return None
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _error(issues, "manifest_invalid_json", f"could not read manifest JSON: {error}")
        return None
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        _error(issues, "manifest_invalid_schema", "manifest root must be an array of objects")
        return None

    records = [cast(dict[str, object], item) for item in raw]
    matching = [
        record
        for record in records
        if _manifest_path_matches(record.get("final_file_path"), file_path, manifest_path)
    ]
    if len(matching) == 1:
        return matching[0]
    if len(matching) > 1:
        _error(
            issues,
            "manifest_record_ambiguous",
            f"manifest contains {len(matching)} records for the same file",
        )
        return None
    if len(records) == 1:
        return records[0]
    _error(
        issues,
        "manifest_record_not_found",
        "manifest contains no unambiguous record for the requested file",
    )
    return None


def _manifest_path_matches(value: object, file_path: Path, manifest_path: Path) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    recorded = Path(value).expanduser()
    candidates = [recorded.resolve()] if recorded.is_absolute() else [
        (Path.cwd() / recorded).resolve(),
        (manifest_path.parent / recorded).resolve(),
    ]
    return file_path in candidates


def _validate_manifest_file_path(
    record: dict[str, object],
    file_path: Path,
    manifest_path: Path,
    issues: list[ValidationIssue],
) -> None:
    if not _manifest_path_matches(record.get("final_file_path"), file_path, manifest_path):
        _error(
            issues,
            "manifest_file_path_mismatch",
            "manifest final_file_path does not identify the validated file",
        )


def _parse_manifest_request(
    record: dict[str, object], issues: list[ValidationIssue]
) -> ManifestRequest | None:
    try:
        dataset = _required_string(record, "dataset")
        variables = _string_tuple(record.get("variables"), "variables")
        output_format = _required_string(record, "output_format")
        file_size_bytes = _positive_integer(record.get("file_size_bytes"), "file_size_bytes")
        digest = _required_string(record, "sha256")
        if len(digest) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in digest
        ):
            raise ValueError("sha256 must contain exactly 64 hexadecimal characters")

        area_mapping = _string_mapping(record.get("area"), "area")
        area = ManifestArea(
            north=_finite_number(area_mapping.get("north"), "area.north"),
            west=_finite_number(area_mapping.get("west"), "area.west"),
            south=_finite_number(area_mapping.get("south"), "area.south"),
            east=_finite_number(area_mapping.get("east"), "area.east"),
        )
        request_parameters = _string_mapping(
            record.get("request_parameters"), "request_parameters"
        )
        year = _single_integer(request_parameters.get("year"), "request_parameters.year")
        month = _single_integer(request_parameters.get("month"), "request_parameters.month")
        request_variables = _string_tuple(
            request_parameters.get("variable"), "request_parameters.variable"
        )
        request_area = _number_tuple(
            request_parameters.get("area"), "request_parameters.area", length=4
        )
        request_format = _required_string(request_parameters, "data_format")
    except ValueError as error:
        _error(issues, "manifest_invalid_schema", str(error))
        return None

    if record.get("schema_version") != "1.0":
        _error(
            issues,
            "manifest_schema_version_unsupported",
            f"unsupported manifest schema version: {record.get('schema_version')!r}",
        )
    if record.get("download_status") != "success":
        _error(
            issues,
            "manifest_download_status_invalid",
            "manifest record must describe a successful download",
        )
    if dataset != ERA5_SINGLE_LEVELS_DATASET:
        _error(
            issues,
            "manifest_dataset_mismatch",
            f"unsupported manifest dataset: {dataset!r}",
        )
    if output_format != "netcdf":
        _error(
            issues,
            "manifest_output_format_mismatch",
            f"manifest output_format must be 'netcdf', got {output_format!r}",
        )
    if not 1940 <= year <= 2100:
        _error(issues, "manifest_year_invalid", f"manifest year is invalid: {year}")
    if not 1 <= month <= 12:
        _error(issues, "manifest_month_invalid", f"manifest month is invalid: {month}")
    if not -90.0 <= area.south < area.north <= 90.0:
        _error(issues, "manifest_area_invalid", "manifest latitude bounds are invalid")
    if not -180.0 <= area.west < area.east <= 180.0:
        _error(issues, "manifest_area_invalid", "manifest longitude bounds are invalid")
    if set(request_variables) != set(variables) or len(request_variables) != len(variables):
        _error(
            issues,
            "manifest_variables_inconsistent",
            "manifest variables differ from request_parameters.variable",
        )
    if request_format != output_format:
        _error(
            issues,
            "manifest_output_format_inconsistent",
            "manifest output_format differs from request_parameters.data_format",
        )
    manifest_area = (area.north, area.west, area.south, area.east)
    if not np.allclose(request_area, manifest_area, rtol=0.0, atol=COORDINATE_TOLERANCE_DEGREES):
        _error(
            issues,
            "manifest_area_inconsistent",
            "manifest area differs from request_parameters.area",
        )

    if 1 <= month <= 12:
        last_day = calendar.monthrange(year, month)[1]
        expected_start = f"{year:04d}-{month:02d}-01T00:00:00Z"
        expected_end = f"{year:04d}-{month:02d}-{last_day:02d}T23:00:00Z"
        if record.get("data_time_start") != expected_start:
            _error(
                issues,
                "manifest_time_start_inconsistent",
                f"manifest data_time_start must be {expected_start}",
            )
        if record.get("data_time_end") != expected_end:
            _error(
                issues,
                "manifest_time_end_inconsistent",
                f"manifest data_time_end must be {expected_end}",
            )

    return ManifestRequest(
        dataset=dataset,
        year=year,
        month=month,
        variables=variables,
        area=area,
        output_format=output_format,
        file_size_bytes=file_size_bytes,
        sha256=digest,
    )


def _validate_dataset(
    dataset: xr.Dataset,
    request: ManifestRequest | None,
    issues: list[ValidationIssue],
) -> DatasetSummary:
    time_name = _resolve_coordinate(dataset, "time", issues)
    latitude_name = _resolve_coordinate(dataset, "latitude", issues)
    longitude_name = _resolve_coordinate(dataset, "longitude", issues)

    if time_name is not None and request is not None:
        time = _validate_time_coordinate(
            dataset[time_name], request.year, request.month, issues
        )
    elif time_name is not None:
        time = _inspect_time_coordinate(dataset[time_name], issues)
    else:
        time = TimeInspection(None, None, None, None, None)

    if latitude_name is not None:
        latitude = _validate_spatial_coordinate(
            dataset[latitude_name], "latitude", request, issues
        )
    else:
        latitude = SpatialInspection(None, None, None, None)
    if longitude_name is not None:
        longitude = _validate_spatial_coordinate(
            dataset[longitude_name], "longitude", request, issues
        )
    else:
        longitude = SpatialInspection(None, None, None, None)

    mappings, variable_summaries = _validate_variables(
        dataset,
        request,
        time.dimension,
        latitude.dimension,
        longitude.dimension,
        issues,
    )
    core_names = {name for name in (time_name, latitude_name, longitude_name) if name}
    auxiliary_coordinates = tuple(
        sorted(str(name) for name in set(dataset.coords) - core_names)
    )
    return DatasetSummary(
        dimensions={str(name): int(size) for name, size in dataset.sizes.items()},
        time_coordinate=time_name,
        time_start=time.start,
        time_end=time.end,
        time_count=time.count,
        time_resolution=time.resolution,
        timezone="UTC",
        latitude_coordinate=latitude_name,
        latitude_min=latitude.minimum,
        latitude_max=latitude.maximum,
        latitude_direction=latitude.direction,
        longitude_coordinate=longitude_name,
        longitude_min=longitude.minimum,
        longitude_max=longitude.maximum,
        longitude_direction=longitude.direction,
        coordinate_tolerance_degrees=COORDINATE_TOLERANCE_DEGREES,
        auxiliary_coordinates=auxiliary_coordinates,
        variable_mappings=mappings,
        variables=tuple(variable_summaries),
    )


def _resolve_coordinate(
    dataset: xr.Dataset, role: str, issues: list[ValidationIssue]
) -> str | None:
    candidates = coordinate_candidates(dataset, role)
    if not candidates:
        aliases = ", ".join(COORDINATE_ALIASES[role])
        _error(
            issues,
            f"{role}_coordinate_missing",
            f"no {role} coordinate found; supported names: {aliases}",
        )
        return None
    if len(candidates) > 1:
        _error(
            issues,
            f"{role}_coordinate_ambiguous",
            f"multiple {role} coordinates found: {', '.join(candidates)}",
        )
    return candidates[0]


def _validate_time_coordinate(
    coordinate: xr.DataArray,
    year: int,
    month: int,
    issues: list[ValidationIssue],
) -> TimeInspection:
    inspection = _inspect_time_coordinate(coordinate, issues)
    if inspection.count is None or inspection.start is None or inspection.end is None:
        return inspection

    expected_count = calendar.monthrange(year, month)[1] * 24
    expected_start = np.datetime64(f"{year:04d}-{month:02d}-01T00:00:00", "ns")
    if month == 12:
        next_month = np.datetime64(f"{year + 1:04d}-01-01T00:00:00", "ns")
    else:
        next_month = np.datetime64(f"{year:04d}-{month + 1:02d}-01T00:00:00", "ns")
    expected_end = next_month - EXPECTED_TIME_STEP

    if inspection.count != expected_count:
        _error(
            issues,
            "time_count_mismatch",
            f"expected {expected_count} hourly timestamps for {year:04d}-{month:02d}, "
            f"found {inspection.count}",
        )
    if (
        inspection.start != _format_datetime(expected_start)
        or inspection.end != _format_datetime(expected_end)
    ):
        _error(
            issues,
            "time_range_mismatch",
            f"time coordinate does not cover the complete requested month {year:04d}-{month:02d}",
        )
    return inspection


def _inspect_time_coordinate(
    coordinate: xr.DataArray, issues: list[ValidationIssue]
) -> TimeInspection:
    if coordinate.ndim != 1:
        _error(
            issues,
            "time_coordinate_not_1d",
            f"time coordinate {coordinate.name!r} must be one-dimensional",
        )
        return TimeInspection(None, None, None, None, None)
    dimension = cast(str, coordinate.dims[0])
    try:
        values = np.asarray(coordinate.values).astype("datetime64[ns]")
    except (TypeError, ValueError) as error:
        _error(issues, "time_parse_failed", f"time coordinate cannot be parsed: {error}")
        return TimeInspection(dimension, None, None, int(coordinate.size), None)
    if values.size == 0 or bool(np.any(np.isnat(values))):
        _error(issues, "time_parse_failed", "time coordinate is empty or contains invalid values")
        return TimeInspection(dimension, None, None, int(values.size), None)

    differences = np.diff(values)
    duplicate_count = int(values.size - np.unique(values).size)
    if duplicate_count:
        _error(
            issues,
            "time_duplicate",
            f"time coordinate contains {duplicate_count} duplicate timestamp(s)",
        )
    if differences.size and not bool(np.all(differences > np.timedelta64(0, "ns"))):
        _error(issues, "time_not_monotonic", "time coordinate is not strictly increasing")
    hourly = not differences.size or bool(np.all(differences == EXPECTED_TIME_STEP))
    if not hourly:
        _error(issues, "time_not_hourly", "time coordinate is not continuous at one-hour steps")
    return TimeInspection(
        dimension=dimension,
        start=_format_datetime(values[0]),
        end=_format_datetime(values[-1]),
        count=int(values.size),
        resolution="1 hour" if hourly else "irregular",
    )


def _format_datetime(value: np.datetime64) -> str:
    return f"{np.datetime_as_string(value, unit='s')}Z"


def _validate_spatial_coordinate(
    coordinate: xr.DataArray,
    role: str,
    request: ManifestRequest | None,
    issues: list[ValidationIssue],
) -> SpatialInspection:
    if coordinate.ndim != 1:
        _error(
            issues,
            f"{role}_coordinate_not_1d",
            f"{role} coordinate {coordinate.name!r} must be a one-dimensional regular axis",
        )
        return SpatialInspection(None, None, None, None)
    dimension = cast(str, coordinate.dims[0])
    try:
        values = np.asarray(coordinate.values, dtype=np.float64)
    except (TypeError, ValueError) as error:
        _error(issues, f"{role}_coordinate_not_numeric", f"{role} is not numeric: {error}")
        return SpatialInspection(dimension, None, None, None)
    if values.size == 0 or not bool(np.all(np.isfinite(values))):
        _error(issues, f"{role}_coordinate_invalid", f"{role} is empty or non-finite")
        return SpatialInspection(dimension, None, None, None)

    minimum = float(np.min(values))
    maximum = float(np.max(values))
    direction = coordinate_direction(values)
    if direction == "non_monotonic":
        _error(issues, f"{role}_not_monotonic", f"{role} must be increasing or decreasing")
    if not is_regular_axis(values, tolerance=COORDINATE_TOLERANCE_DEGREES):
        _error(issues, f"{role}_not_regular", f"{role} is not a regular one-dimensional axis")

    duplicate_values = values if role == "latitude" else _longitude_near(values, 0.0)
    if _contains_duplicate_values(duplicate_values):
        _error(issues, f"{role}_duplicate", f"{role} contains duplicate coordinate values")

    if role == "latitude":
        if minimum < -90.0 or maximum > 90.0:
            _error(issues, "latitude_out_of_range", "latitude must be within [-90, 90]")
        if request is not None and (
            minimum > request.area.south + COORDINATE_TOLERANCE_DEGREES
            or maximum < request.area.north - COORDINATE_TOLERANCE_DEGREES
        ):
            _error(
                issues,
                "requested_area_not_covered",
                "latitude extent does not cover the manifest request area",
            )
    else:
        in_signed_range = minimum >= -180.0 and maximum <= 180.0
        in_unsigned_range = minimum >= 0.0 and maximum <= 360.0
        if not (in_signed_range or in_unsigned_range):
            _error(
                issues,
                "longitude_out_of_range",
                "longitude must use either [-180, 180] or [0, 360]",
            )
        if request is not None and not _longitude_covers(values, request.area):
            _error(
                issues,
                "requested_area_not_covered",
                "longitude extent does not cover the manifest request area",
            )
    return SpatialInspection(dimension, minimum, maximum, direction)


def _longitude_near(values: NDArray[np.float64], center: float) -> NDArray[np.float64]:
    return ((values - center + 180.0) % 360.0) + center - 180.0


def _longitude_covers(values: NDArray[np.float64], area: ManifestArea) -> bool:
    center = (area.west + area.east) / 2.0
    comparable = _longitude_near(values, center)
    return bool(
        np.min(comparable) <= area.west + COORDINATE_TOLERANCE_DEGREES
        and np.max(comparable) >= area.east - COORDINATE_TOLERANCE_DEGREES
    )


def _contains_duplicate_values(values: NDArray[np.float64]) -> bool:
    if values.size <= 1:
        return False
    ordered = np.sort(values)
    return bool(np.any(np.diff(ordered) <= COORDINATE_TOLERANCE_DEGREES))


def _validate_variables(
    dataset: xr.Dataset,
    request: ManifestRequest | None,
    time_dimension: str | None,
    latitude_dimension: str | None,
    longitude_dimension: str | None,
    issues: list[ValidationIssue],
) -> tuple[dict[str, str | None], list[VariableSummary]]:
    if request is None:
        return {}, []

    mappings: dict[str, str | None] = {}
    specs: list[Era5VariableSpec] = []
    for requested_name in request.variables:
        spec = get_variable_spec(requested_name)
        mappings[requested_name] = None if spec is None else spec.internal_name
        if spec is None:
            _error(
                issues,
                "unknown_requested_variable",
                f"no explicit NetCDF mapping exists for requested variable {requested_name!r}",
                variable=requested_name,
            )
        else:
            specs.append(spec)

    expected_internal = {spec.internal_name for spec in specs}
    unexpected = sorted(str(name) for name in set(dataset.data_vars) - expected_internal)
    for internal_name in unexpected:
        _error(
            issues,
            "unexpected_internal_variable",
            f"NetCDF contains unrequested data variable {internal_name!r}",
            variable=internal_name,
        )

    core_dimensions = {
        dimension
        for dimension in (time_dimension, latitude_dimension, longitude_dimension)
        if dimension is not None
    }
    reference_grid: tuple[int, int, int] | None = None
    summaries: list[VariableSummary] = []
    for spec in specs:
        if spec.internal_name not in dataset.data_vars:
            _error(
                issues,
                "requested_variable_missing",
                f"requested {spec.requested_name!r} maps to missing NetCDF variable "
                f"{spec.internal_name!r}",
                variable=spec.requested_name,
            )
            continue
        data = dataset[spec.internal_name]
        for role, dimension in (
            ("time", time_dimension),
            ("latitude", latitude_dimension),
            ("longitude", longitude_dimension),
        ):
            if dimension is not None and dimension not in data.dims:
                _error(
                    issues,
                    f"variable_missing_{role}_dimension",
                    f"{spec.internal_name!r} does not contain the {role} dimension {dimension!r}",
                    variable=spec.requested_name,
                )
        extra_dimensions = sorted(str(name) for name in set(data.dims) - core_dimensions)
        if extra_dimensions:
            _error(
                issues,
                "variable_unexpected_dimensions",
                f"{spec.internal_name!r} has unexpected dimensions: {', '.join(extra_dimensions)}",
                variable=spec.requested_name,
            )
        for dimension in core_dimensions.intersection(data.dims):
            if int(data.sizes[dimension]) != int(dataset.sizes[dimension]):
                _error(
                    issues,
                    "variable_shape_mismatch",
                    f"{spec.internal_name!r} size for {dimension!r} differs from its coordinate",
                    variable=spec.requested_name,
                )

        if all(
            dimension is not None and dimension in data.dims
            for dimension in (time_dimension, latitude_dimension, longitude_dimension)
        ):
            assert time_dimension is not None
            assert latitude_dimension is not None
            assert longitude_dimension is not None
            grid = (
                int(data.sizes[time_dimension]),
                int(data.sizes[latitude_dimension]),
                int(data.sizes[longitude_dimension]),
            )
            if reference_grid is None:
                reference_grid = grid
            elif grid != reference_grid:
                _error(
                    issues,
                    "variable_core_grid_mismatch",
                    f"{spec.internal_name!r} core grid {grid} differs from {reference_grid}",
                    variable=spec.requested_name,
                )

        units_value = data.attrs.get("units")
        units = (
            units_value.strip()
            if isinstance(units_value, str) and units_value.strip()
            else None
        )
        if units is None:
            _warning(
                issues,
                "variable_units_missing",
                f"{spec.internal_name!r} has no units attribute; expected {spec.canonical_units!r}",
                variable=spec.requested_name,
            )
        elif normalize_units(units) not in spec.accepted_units:
            _error(
                issues,
                "variable_units_mismatch",
                f"{spec.internal_name!r} units {units!r} conflict with expected "
                f"{spec.canonical_units!r}",
                variable=spec.requested_name,
            )

        try:
            statistics = compute_numeric_statistics(data)
        except TypeError as error:
            _error(
                issues,
                "variable_not_numeric",
                str(error),
                variable=spec.requested_name,
            )
            summaries.append(
                VariableSummary(
                    requested_name=spec.requested_name,
                    internal_name=spec.internal_name,
                    dimensions=tuple(str(dimension) for dimension in data.dims),
                    shape=tuple(int(size) for size in data.shape),
                    units=units,
                    total_count=int(data.size),
                    nan_count=0,
                    non_finite_count=int(data.size),
                    missing_ratio=0.0,
                    finite_ratio=0.0,
                    minimum=None,
                    maximum=None,
                    mean=None,
                    standard_deviation=None,
                )
            )
            continue

        if statistics.finite_count == 0:
            _error(
                issues,
                "variable_all_non_finite",
                f"{spec.internal_name!r} contains no finite values",
                variable=spec.requested_name,
            )
        elif statistics.nan_count:
            _warning(
                issues,
                "variable_missing_values",
                f"{spec.internal_name!r} contains {statistics.nan_count} NaN value(s)",
                variable=spec.requested_name,
            )
        infinite_count = statistics.non_finite_count - statistics.nan_count
        if infinite_count > 0:
            _error(
                issues,
                "variable_infinite_values",
                f"{spec.internal_name!r} contains {infinite_count} infinite value(s)",
                variable=spec.requested_name,
            )
        if statistics.minimum is not None and statistics.minimum < spec.safe_minimum:
            _error(
                issues,
                "variable_below_safe_range",
                f"{spec.internal_name!r} minimum {statistics.minimum:g} is below broad integrity "
                f"bound {spec.safe_minimum:g} {spec.canonical_units}",
                variable=spec.requested_name,
            )
        if statistics.maximum is not None and statistics.maximum > spec.safe_maximum:
            _error(
                issues,
                "variable_above_safe_range",
                f"{spec.internal_name!r} maximum {statistics.maximum:g} is above broad integrity "
                f"bound {spec.safe_maximum:g} {spec.canonical_units}",
                variable=spec.requested_name,
            )

        total = statistics.total_count
        summaries.append(
            VariableSummary(
                requested_name=spec.requested_name,
                internal_name=spec.internal_name,
                dimensions=tuple(str(dimension) for dimension in data.dims),
                shape=tuple(int(size) for size in data.shape),
                units=units,
                total_count=total,
                nan_count=statistics.nan_count,
                non_finite_count=statistics.non_finite_count,
                missing_ratio=statistics.nan_count / total if total else 0.0,
                finite_ratio=statistics.finite_count / total if total else 0.0,
                minimum=statistics.minimum,
                maximum=statistics.maximum,
                mean=statistics.mean,
                standard_deviation=statistics.standard_deviation,
            )
        )
    return mappings, summaries


def _required_string(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest field {key} must be a non-empty string")
    return value.strip()


def _string_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"manifest field {name} must be an object with string keys")
    return cast(dict[str, object], value)


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"manifest field {name} must be a non-empty string array")
    result = tuple(cast(str, item).strip() for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"manifest field {name} must not contain duplicates")
    return result


def _number_tuple(value: object, name: str, *, length: int) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"manifest field {name} must contain exactly {length} numbers")
    return tuple(_finite_number(item, name) for item in value)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"manifest field {name} must be a finite number")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"manifest field {name} must be a finite number")
    return result


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"manifest field {name} must be a positive integer")
    return value


def _single_integer(value: object, name: str) -> int:
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError(f"manifest field {name} must contain exactly one value")
    item = value[0]
    if isinstance(item, bool):
        raise ValueError(f"manifest field {name} must contain an integer")
    if isinstance(item, int):
        return item
    if isinstance(item, str) and item.isascii() and item.isdigit():
        return int(item)
    raise ValueError(f"manifest field {name} must contain an integer")


def _error(
    issues: list[ValidationIssue],
    code: str,
    message: str,
    *,
    variable: str | None = None,
) -> None:
    issues.append(ValidationIssue("error", code, message, variable))


def _warning(
    issues: list[ValidationIssue],
    code: str,
    message: str,
    *,
    variable: str | None = None,
) -> None:
    issues.append(ValidationIssue("warning", code, message, variable))


def _report(
    file_path: Path,
    manifest_path: Path,
    issues: list[ValidationIssue],
    *,
    dataset: DatasetSummary | None,
    file_size_bytes: int | None = None,
    file_sha256: str | None = None,
    file_sha256_after_validation: str | None = None,
) -> ValidationReport:
    return ValidationReport(
        file_path=str(file_path),
        manifest_path=str(manifest_path),
        file_size_bytes=file_size_bytes,
        file_sha256=file_sha256,
        file_sha256_after_validation=file_sha256_after_validation,
        status=validation_status(issues),
        issues=tuple(issues),
        dataset=dataset,
    )
