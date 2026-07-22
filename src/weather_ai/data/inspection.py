"""Lazy NetCDF opening and bounded-memory numeric inspection."""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
from numpy.typing import NDArray

NETCDF_ENGINE = "h5netcdf"
MAX_STATISTICS_CHUNK_ELEMENTS = 1_000_000
COORDINATE_ALIASES: dict[str, tuple[str, ...]] = {
    "time": ("valid_time", "time"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon"),
}


@dataclass(frozen=True, slots=True)
class NumericStatistics:
    """Exact streaming statistics over finite values."""

    total_count: int
    nan_count: int
    non_finite_count: int
    finite_count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    standard_deviation: float | None


@contextmanager
def open_era5_dataset(path: Path) -> Iterator[xr.Dataset]:
    """Open one NetCDF read-only and lazily, then always close its file handle."""

    dataset = xr.open_dataset(
        path,
        engine=NETCDF_ENGINE,
        chunks=None,
        cache=False,
        decode_cf=True,
        mask_and_scale=True,
        decode_times=True,
    )
    try:
        yield dataset
    finally:
        dataset.close()


def coordinate_candidates(dataset: xr.Dataset, role: str) -> tuple[str, ...]:
    """Return explicitly supported coordinate aliases present for a semantic role."""

    aliases = COORDINATE_ALIASES[role]
    return tuple(name for name in aliases if name in dataset.coords)


def coordinate_direction(values: NDArray[np.float64]) -> str:
    """Classify a one-dimensional coordinate without reordering it."""

    if values.size <= 1:
        return "singleton"
    differences = np.diff(values)
    if bool(np.all(differences > 0.0)):
        return "increasing"
    if bool(np.all(differences < 0.0)):
        return "decreasing"
    return "non_monotonic"


def is_regular_axis(values: NDArray[np.float64], *, tolerance: float) -> bool:
    """Return whether adjacent coordinate steps have one constant signed spacing."""

    if values.size <= 2:
        return True
    differences = np.diff(values)
    return bool(np.allclose(differences, differences[0], rtol=0.0, atol=tolerance))


def compute_numeric_statistics(data: xr.DataArray) -> NumericStatistics:
    """Compute exact population statistics while bounding each in-memory chunk.

    Values are streamed along the largest dimension. NaNs are counted separately;
    ``non_finite_count`` includes both NaN and positive/negative infinity.
    """

    total_count = int(data.size)
    finite_count = 0
    nan_count = 0
    running_mean = 0.0
    running_m2 = 0.0
    minimum: float | None = None
    maximum: float | None = None

    for raw_chunk in _iter_value_chunks(data):
        try:
            chunk = np.asarray(raw_chunk, dtype=np.float64)
        except (TypeError, ValueError) as error:
            raise TypeError(f"variable {data.name!r} is not numeric") from error

        chunk_nan = np.isnan(chunk)
        chunk_finite = np.isfinite(chunk)
        nan_count += int(np.count_nonzero(chunk_nan))
        finite_values = chunk[chunk_finite]
        chunk_count = int(finite_values.size)
        if chunk_count == 0:
            continue

        chunk_minimum = float(np.min(finite_values))
        chunk_maximum = float(np.max(finite_values))
        minimum = chunk_minimum if minimum is None else min(minimum, chunk_minimum)
        maximum = chunk_maximum if maximum is None else max(maximum, chunk_maximum)

        chunk_mean = float(np.mean(finite_values, dtype=np.float64))
        chunk_m2 = float(np.sum((finite_values - chunk_mean) ** 2, dtype=np.float64))
        if finite_count == 0:
            running_mean = chunk_mean
            running_m2 = chunk_m2
        else:
            combined_count = finite_count + chunk_count
            delta = chunk_mean - running_mean
            running_mean += delta * chunk_count / combined_count
            running_m2 += (
                chunk_m2
                + delta * delta * finite_count * chunk_count / combined_count
            )
        finite_count += chunk_count

    non_finite_count = total_count - finite_count
    standard_deviation = (
        math.sqrt(max(running_m2, 0.0) / finite_count) if finite_count else None
    )
    return NumericStatistics(
        total_count=total_count,
        nan_count=nan_count,
        non_finite_count=non_finite_count,
        finite_count=finite_count,
        minimum=minimum,
        maximum=maximum,
        mean=running_mean if finite_count else None,
        standard_deviation=standard_deviation,
    )


def _iter_value_chunks(data: xr.DataArray) -> Iterator[NDArray[Any]]:
    if data.ndim == 0:
        yield np.asarray(data.values)
        return

    chunk_dimension = max(data.dims, key=lambda name: int(data.sizes[name]))
    dimension_size = int(data.sizes[chunk_dimension])
    other_elements = max(1, int(data.size) // max(1, dimension_size))
    chunk_length = max(1, MAX_STATISTICS_CHUNK_ELEMENTS // other_elements)
    for start in range(0, dimension_size, chunk_length):
        stop = min(start + chunk_length, dimension_size)
        yield np.asarray(data.isel({chunk_dimension: slice(start, stop)}).values)
