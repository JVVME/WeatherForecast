"""Dynamic, offline NetCDF and manifest fixtures for M1-B tests."""

from __future__ import annotations

import calendar
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import xarray as xr
from numpy.typing import NDArray

REQUESTED_VARIABLES = (
    "2m_temperature",
    "2m_dewpoint_temperature",
    "surface_pressure",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
)
INTERNAL_NAMES = {
    "2m_temperature": "t2m",
    "2m_dewpoint_temperature": "d2m",
    "surface_pressure": "sp",
    "10m_u_component_of_wind": "u10",
    "10m_v_component_of_wind": "v10",
}
DEFAULT_UNITS = {
    "t2m": "K",
    "d2m": "K",
    "sp": "Pa",
    "u10": "m s**-1",
    "v10": "m s**-1",
}
DEFAULT_VALUES = {
    "t2m": 280.0,
    "d2m": 275.0,
    "sp": 100_000.0,
    "u10": 3.0,
    "v10": -1.0,
}
DEFAULT_AREA = (31.75, 120.75, 30.75, 122.0)


def complete_month_times(year: int = 2024, month: int = 2) -> NDArray[np.datetime64]:
    """Return every hourly timestamp in one complete calendar month."""

    start = np.datetime64(f"{year:04d}-{month:02d}-01T00:00:00", "h")
    if month == 12:
        stop = np.datetime64(f"{year + 1:04d}-01-01T00:00:00", "h")
    else:
        stop = np.datetime64(f"{year:04d}-{month + 1:02d}-01T00:00:00", "h")
    return np.arange(start, stop, np.timedelta64(1, "h"))


def write_era5_netcdf(
    path: Path,
    *,
    times: NDArray[np.datetime64] | None = None,
    latitudes: NDArray[np.float64] | None = None,
    longitudes: NDArray[np.float64] | None = None,
    omitted_variables: frozenset[str] = frozenset(),
    unit_overrides: Mapping[str, str | None] | None = None,
    value_overrides: Mapping[str, float] | None = None,
    dimension_mismatch_variable: str | None = None,
    all_nan_variable: str | None = None,
    infinite_variable: str | None = None,
    time_coordinate_name: str = "valid_time",
) -> Path:
    """Create a tiny ERA5-style NetCDF without network access."""

    observed_times = complete_month_times() if times is None else times
    observed_latitudes = (
        np.array([31.75, 31.5, 31.25, 31.0, 30.75], dtype=np.float64)
        if latitudes is None
        else latitudes
    )
    observed_longitudes = (
        np.array([120.75, 121.0, 121.25, 121.5, 121.75, 122.0], dtype=np.float64)
        if longitudes is None
        else longitudes
    )
    coordinates: dict[str, object] = {
        time_coordinate_name: observed_times,
        "latitude": observed_latitudes,
        "longitude": observed_longitudes,
    }
    data_variables: dict[str, xr.DataArray] = {}
    units: dict[str, str | None] = dict(DEFAULT_UNITS)
    if unit_overrides is not None:
        units.update(unit_overrides)
    values = dict(DEFAULT_VALUES)
    if value_overrides is not None:
        values.update(value_overrides)

    normal_shape = (
        observed_times.size,
        observed_latitudes.size,
        observed_longitudes.size,
    )
    for requested_name in REQUESTED_VARIABLES:
        internal_name = INTERNAL_NAMES[requested_name]
        if internal_name in omitted_variables:
            continue
        dimensions = (time_coordinate_name, "latitude", "longitude")
        shape = normal_shape
        if internal_name == dimension_mismatch_variable:
            coordinates["other_longitude"] = observed_longitudes
            dimensions = (time_coordinate_name, "latitude", "other_longitude")

        array = np.full(shape, values[internal_name], dtype=np.float32)
        if internal_name == all_nan_variable:
            array.fill(np.nan)
        if internal_name == infinite_variable:
            array.reshape(-1)[0] = np.inf
        attributes = {} if units.get(internal_name) is None else {"units": units[internal_name]}
        data_variables[internal_name] = xr.DataArray(
            array,
            dims=dimensions,
            attrs=attributes,
        )

    dataset = xr.Dataset(data_vars=data_variables, coords=coordinates)
    try:
        dataset.to_netcdf(path, engine="h5netcdf")
    finally:
        dataset.close()
    return path


def write_manifest(
    path: Path,
    netcdf_path: Path,
    *,
    variables: tuple[str, ...] = REQUESTED_VARIABLES,
    area: tuple[float, float, float, float] = DEFAULT_AREA,
    year: int = 2024,
    month: int = 2,
    sha256: str | None = None,
    file_size_bytes: int | None = None,
    final_file_path: str | None = None,
) -> Path:
    """Write one M1-A-compatible manifest record for a generated NetCDF."""

    last_day = calendar.monthrange(year, month)[1]
    payload = netcdf_path.read_bytes()
    record = {
        "schema_version": "1.0",
        "dataset": "reanalysis-era5-single-levels",
        "requested_at": "2026-07-22T00:00:00+00:00",
        "data_time_start": f"{year:04d}-{month:02d}-01T00:00:00Z",
        "data_time_end": f"{year:04d}-{month:02d}-{last_day:02d}T23:00:00Z",
        "variables": list(variables),
        "area": {
            "id": "test-area",
            "north": area[0],
            "west": area[1],
            "south": area[2],
            "east": area[3],
        },
        "output_format": "netcdf",
        "final_file_path": final_file_path or str(netcdf_path.resolve()),
        "file_size_bytes": len(payload) if file_size_bytes is None else file_size_bytes,
        "sha256": sha256 or hashlib.sha256(payload).hexdigest(),
        "request_parameters": {
            "area": list(area),
            "data_format": "netcdf",
            "download_format": "unarchived",
            "month": [f"{month:02d}"],
            "product_type": ["reanalysis"],
            "variable": list(variables),
            "year": [str(year)],
        },
        "project_version": "0.1.0",
        "git_commit": None,
        "download_status": "success",
    }
    path.write_text(json.dumps([record], indent=2), encoding="utf-8")
    return path
