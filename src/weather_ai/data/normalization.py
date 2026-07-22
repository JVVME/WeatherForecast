"""Pure conversions and structural normalization for M2-A ERA5 data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
import xarray as xr

from weather_ai.data.errors import PreprocessingValidationError
from weather_ai.data.inspection import (
    COORDINATE_ALIASES,
    compute_numeric_statistics,
    coordinate_candidates,
    coordinate_direction,
)
from weather_ai.data.preprocessing_schemas import (
    CoordinateNormalizationRecord,
    PreprocessingVariableSummary,
    UnitConversionRecord,
)
from weather_ai.data.variables import Era5VariableSpec, get_variable_spec, normalize_units

CORE_DIMENSIONS = ("time", "latitude", "longitude")


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """Normalized in-memory xarray graph and its auditable decisions."""

    dataset: xr.Dataset
    conversions: tuple[UnitConversionRecord, ...]
    coordinates: CoordinateNormalizationRecord


def convert_variable_units(data: xr.DataArray, spec: Era5VariableSpec) -> xr.DataArray:
    """Apply the exact unit conversion declared by ``spec`` without guessing units."""

    raw_units = data.attrs.get("units")
    if not isinstance(raw_units, str) or not raw_units.strip():
        raise PreprocessingValidationError(
            f"source variable {spec.internal_name!r} has no units; expected "
            f"{spec.canonical_units!r}"
        )
    if normalize_units(raw_units) not in spec.accepted_units:
        raise PreprocessingValidationError(
            f"source variable {spec.internal_name!r} units {raw_units!r} do not match "
            f"required {spec.canonical_units!r}"
        )

    if spec.conversion == "kelvin_to_celsius":
        return data - 273.15
    if spec.conversion == "pascal_to_hectopascal":
        return data / 100.0
    if spec.conversion == "identity":
        return data.copy(deep=False)
    raise AssertionError(f"unhandled unit conversion: {spec.conversion}")


def normalize_era5_dataset(
    source: xr.Dataset,
    expected_variables: tuple[str, ...],
    *,
    dtype: str,
) -> NormalizationResult:
    """Normalize names, units, dimensions, and coordinate order without interpolation."""

    source_names, source_dimensions, input_directions = _inspect_core_coordinates(source)
    canonical = _canonicalize_core_coordinates(source, source_names, source_dimensions)

    specs = _expected_specs(expected_variables)
    expected_internal = {spec.internal_name for spec in specs}
    observed_internal = {str(name) for name in canonical.data_vars}
    unknown = sorted(observed_internal - expected_internal)
    missing = sorted(expected_internal - observed_internal)
    if unknown:
        raise PreprocessingValidationError(
            f"source contains unknown or unrequested data variable(s): {', '.join(unknown)}"
        )
    if missing:
        raise PreprocessingValidationError(
            f"source is missing required data variable(s): {', '.join(missing)}"
        )

    output_arrays: dict[str, xr.DataArray] = {}
    conversions: list[UnitConversionRecord] = []
    removed_dimensions: set[str] = set()
    for spec in specs:
        raw = canonical[spec.internal_name]
        raw = _normalize_dimensions(raw, spec, removed_dimensions)
        raw_statistics = _validate_source_values(raw, spec)
        converted = convert_variable_units(raw, spec).astype(dtype)
        raw_units = cast(str, raw.attrs["units"]).strip()
        converted = converted.transpose(*CORE_DIMENSIONS)
        converted.name = spec.output_name
        converted.attrs = {
            **raw.attrs,
            "units": spec.output_units,
            "source_variable": spec.internal_name,
            "source_units": raw_units,
            "unit_conversion": spec.conversion_expression,
        }
        output_statistics = _validate_output_values(converted, spec)
        if output_statistics.nan_count != raw_statistics.nan_count:
            raise PreprocessingValidationError(
                f"unit conversion for {spec.internal_name!r} changed the NaN count"
            )
        output_arrays[spec.output_name] = converted
        conversions.append(
            UnitConversionRecord(
                input_name=spec.internal_name,
                output_name=spec.output_name,
                input_units=raw_units,
                output_units=spec.output_units,
                operation=spec.conversion_expression,
            )
        )

    normalized = xr.Dataset(data_vars=output_arrays, attrs=dict(source.attrs))
    sorted_coordinates: list[str] = []
    for coordinate_name in ("latitude", "longitude"):
        direction = coordinate_direction(
            np.asarray(normalized[coordinate_name].values, dtype=np.float64)
        )
        if direction == "decreasing":
            normalized = normalized.sortby(coordinate_name)
            sorted_coordinates.append(coordinate_name)
        elif direction not in {"increasing", "singleton"}:
            raise PreprocessingValidationError(
                f"{coordinate_name} must be strictly increasing or decreasing"
            )

    _set_coordinate_metadata(normalized)
    normalized.attrs.update(
        {
            "Conventions": "CF-1.8",
            "preprocessing_stage": "M2-A",
            "time_semantics": "UTC",
            "longitude_domain_conversion": "none",
        }
    )
    extra_dimensions = sorted(
        str(name) for name in set(normalized.sizes) - set(CORE_DIMENSIONS)
    )
    if extra_dimensions:
        raise PreprocessingValidationError(
            f"normalized dataset retains unsupported dimensions: {', '.join(extra_dimensions)}"
        )

    output_directions = {
        name: _coordinate_direction(normalized[name], name) for name in CORE_DIMENSIONS
    }
    auxiliary_coordinates = tuple(
        sorted(str(name) for name in set(normalized.coords) - set(CORE_DIMENSIONS))
    )
    return NormalizationResult(
        dataset=normalized,
        conversions=tuple(conversions),
        coordinates=CoordinateNormalizationRecord(
            source_names=source_names,
            input_directions=input_directions,
            output_directions=output_directions,
            sorted_coordinates=tuple(sorted_coordinates),
            auxiliary_dimensions_removed=tuple(sorted(removed_dimensions)),
            auxiliary_coordinates_preserved=auxiliary_coordinates,
        ),
    )


def validate_normalized_dataset(
    dataset: xr.Dataset,
    expected_variables: tuple[str, ...],
    *,
    dtype: str,
) -> tuple[PreprocessingVariableSummary, ...]:
    """Validate a re-opened temporary M2-A file before atomic publication."""

    observed_dimensions = set(dataset.sizes)
    if observed_dimensions != set(CORE_DIMENSIONS):
        raise PreprocessingValidationError(
            f"normalized dimensions must be exactly {CORE_DIMENSIONS}, got "
            f"{tuple(dataset.sizes)}"
        )
    for name in CORE_DIMENSIONS:
        if name not in dataset.coords:
            raise PreprocessingValidationError(f"normalized coordinate {name!r} is missing")

    time_values = _datetime_values(dataset["time"])
    time_differences = np.diff(time_values)
    if time_differences.size and not bool(
        np.all(time_differences > np.timedelta64(0, "ns"))
    ):
        raise PreprocessingValidationError(
            "normalized time must be strictly increasing with no duplicates"
        )
    for name in ("latitude", "longitude"):
        if _coordinate_direction(dataset[name], name) not in {"increasing", "singleton"}:
            raise PreprocessingValidationError(f"normalized {name} must be increasing")

    specs = _expected_specs(expected_variables)
    expected_output = {spec.output_name for spec in specs}
    observed_output = {str(name) for name in dataset.data_vars}
    if observed_output != expected_output:
        raise PreprocessingValidationError(
            "normalized variables differ from the explicit M2-A mapping"
        )

    summaries: list[PreprocessingVariableSummary] = []
    for spec in specs:
        data = dataset[spec.output_name]
        if tuple(str(dimension) for dimension in data.dims) != CORE_DIMENSIONS:
            raise PreprocessingValidationError(
                f"{spec.output_name!r} dimensions must be {CORE_DIMENSIONS}"
            )
        if str(data.dtype) != dtype:
            raise PreprocessingValidationError(
                f"{spec.output_name!r} dtype must be {dtype}, got {data.dtype}"
            )
        if data.attrs.get("units") != spec.output_units:
            raise PreprocessingValidationError(
                f"{spec.output_name!r} units must be {spec.output_units!r}"
            )
        statistics = _validate_output_values(data, spec)
        summaries.append(
            PreprocessingVariableSummary(
                name=spec.output_name,
                units=spec.output_units,
                dtype=str(data.dtype),
                shape=tuple(int(size) for size in data.shape),
                nan_count=statistics.nan_count,
                non_finite_count=statistics.non_finite_count,
                minimum=statistics.minimum,
                maximum=statistics.maximum,
                mean=statistics.mean,
                standard_deviation=statistics.standard_deviation,
            )
        )
    return tuple(summaries)


def _expected_specs(expected_variables: tuple[str, ...]) -> tuple[Era5VariableSpec, ...]:
    specs: list[Era5VariableSpec] = []
    for requested_name in expected_variables:
        spec = get_variable_spec(requested_name)
        if spec is None:
            raise PreprocessingValidationError(
                f"unknown ERA5 requested variable: {requested_name!r}"
            )
        specs.append(spec)
    return tuple(specs)


def _inspect_core_coordinates(
    dataset: xr.Dataset,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    source_names: dict[str, str] = {}
    dimensions: dict[str, str] = {}
    directions: dict[str, str] = {}
    for role in CORE_DIMENSIONS:
        candidates = coordinate_candidates(dataset, role)
        if len(candidates) != 1:
            aliases = ", ".join(COORDINATE_ALIASES[role])
            raise PreprocessingValidationError(
                f"source must contain exactly one {role} coordinate from: {aliases}"
            )
        name = candidates[0]
        coordinate = dataset[name]
        if coordinate.ndim != 1 or coordinate.size == 0:
            raise PreprocessingValidationError(f"source {role} coordinate must be non-empty and 1D")
        source_names[role] = name
        dimensions[role] = str(coordinate.dims[0])
        directions[role] = _coordinate_direction(coordinate, role)

    if directions["time"] != "increasing":
        raise PreprocessingValidationError(
            "source time must be strictly increasing with no duplicates"
        )
    for role in ("latitude", "longitude"):
        if directions[role] not in {"increasing", "decreasing", "singleton"}:
            raise PreprocessingValidationError(
                f"source {role} must be strictly increasing or decreasing"
            )
    return source_names, dimensions, directions


def _canonicalize_core_coordinates(
    dataset: xr.Dataset,
    source_names: dict[str, str],
    source_dimensions: dict[str, str],
) -> xr.Dataset:
    normalized = dataset
    for role in CORE_DIMENSIONS:
        source_name = source_names[role]
        source_dimension = source_dimensions[role]
        if source_dimension != source_name:
            normalized = normalized.swap_dims({source_dimension: source_name})
        if source_name != role:
            normalized = normalized.rename({source_name: role})
    return normalized


def _normalize_dimensions(
    data: xr.DataArray,
    spec: Era5VariableSpec,
    removed_dimensions: set[str],
) -> xr.DataArray:
    missing = [dimension for dimension in CORE_DIMENSIONS if dimension not in data.dims]
    if missing:
        raise PreprocessingValidationError(
            f"source variable {spec.internal_name!r} is missing dimension(s): {', '.join(missing)}"
        )
    extras = [str(dimension) for dimension in data.dims if dimension not in CORE_DIMENSIONS]
    meaningful = [dimension for dimension in extras if int(data.sizes[dimension]) != 1]
    if meaningful:
        raise PreprocessingValidationError(
            f"source variable {spec.internal_name!r} has unsupported meaningful dimension(s): "
            f"{', '.join(meaningful)}"
        )
    if extras:
        removed_dimensions.update(extras)
        data = data.squeeze(extras, drop=True)
    return data.transpose(*CORE_DIMENSIONS)


def _validate_source_values(data: xr.DataArray, spec: Era5VariableSpec):  # type: ignore[no-untyped-def]
    if data.dtype not in (np.dtype("float32"), np.dtype("float64")):
        raise PreprocessingValidationError(
            f"source variable {spec.internal_name!r} must be float32 or float64, got {data.dtype}"
        )
    statistics = compute_numeric_statistics(data)
    infinite_count = statistics.non_finite_count - statistics.nan_count
    if infinite_count:
        raise PreprocessingValidationError(
            f"source variable {spec.internal_name!r} contains {infinite_count} infinite value(s)"
        )
    return statistics


def _validate_output_values(data: xr.DataArray, spec: Era5VariableSpec):  # type: ignore[no-untyped-def]
    statistics = compute_numeric_statistics(data)
    infinite_count = statistics.non_finite_count - statistics.nan_count
    if infinite_count:
        raise PreprocessingValidationError(
            f"normalized variable {spec.output_name!r} contains {infinite_count} infinite value(s)"
        )
    if statistics.finite_count == 0:
        raise PreprocessingValidationError(
            f"normalized variable {spec.output_name!r} contains no finite values"
        )
    if statistics.minimum is not None and statistics.minimum < spec.output_safe_minimum:
        raise PreprocessingValidationError(
            f"normalized variable {spec.output_name!r} minimum {statistics.minimum:g} is below "
            f"{spec.output_safe_minimum:g} {spec.output_units}"
        )
    if statistics.maximum is not None and statistics.maximum > spec.output_safe_maximum:
        raise PreprocessingValidationError(
            f"normalized variable {spec.output_name!r} maximum {statistics.maximum:g} is above "
            f"{spec.output_safe_maximum:g} {spec.output_units}"
        )
    return statistics


def _coordinate_direction(coordinate: xr.DataArray, role: str) -> str:
    if role == "time":
        values = _datetime_values(coordinate)
        if values.size <= 1:
            return "singleton"
        differences = np.diff(values)
        if bool(np.all(differences > np.timedelta64(0, "ns"))):
            return "increasing"
        if bool(np.all(differences < np.timedelta64(0, "ns"))):
            return "decreasing"
        return "non_monotonic"
    try:
        values = np.asarray(coordinate.values, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise PreprocessingValidationError(f"source {role} coordinate is not numeric") from error
    if not bool(np.all(np.isfinite(values))):
        raise PreprocessingValidationError(f"source {role} coordinate contains non-finite values")
    return coordinate_direction(values)


def _datetime_values(coordinate: xr.DataArray) -> np.ndarray:
    try:
        values = np.asarray(coordinate.values).astype("datetime64[ns]")
    except (TypeError, ValueError) as error:
        raise PreprocessingValidationError("source time coordinate cannot be decoded") from error
    if values.size == 0 or bool(np.any(np.isnat(values))):
        raise PreprocessingValidationError("source time coordinate is empty or invalid")
    return values


def _set_coordinate_metadata(dataset: xr.Dataset) -> None:
    dataset["time"].attrs.update({"standard_name": "time", "timezone": "UTC"})
    dataset["latitude"].attrs.update(
        {"standard_name": "latitude", "units": "degrees_north", "stored_direction": "increasing"}
    )
    dataset["longitude"].attrs.update(
        {"standard_name": "longitude", "units": "degrees_east", "stored_direction": "increasing"}
    )
