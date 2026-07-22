from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.data_validation_helpers import (
    REQUESTED_VARIABLES,
    complete_month_times,
    write_era5_netcdf,
    write_manifest,
)
from weather_ai.data.schemas import ValidationReport
from weather_ai.data.validation import validate_era5_file


def _validate(tmp_path: Path, **netcdf_options: Any) -> ValidationReport:
    netcdf = write_era5_netcdf(tmp_path / "sample.nc", **netcdf_options)
    manifest = write_manifest(tmp_path / "manifest.json", netcdf)
    return validate_era5_file(netcdf, manifest)


def _codes(report: ValidationReport) -> set[str]:
    return {issue.code for issue in report.issues}


def test_valid_era5_style_netcdf_passes_with_complete_summary(tmp_path: Path) -> None:
    report = _validate(tmp_path)

    assert report.status == "passed"
    assert report.error_count == 0
    assert report.warning_count == 0
    assert report.dataset is not None
    assert report.dataset.dimensions == {"valid_time": 696, "latitude": 5, "longitude": 6}
    assert report.dataset.time_start == "2024-02-01T00:00:00Z"
    assert report.dataset.time_end == "2024-02-29T23:00:00Z"
    assert report.dataset.timezone == "UTC"
    assert report.dataset.latitude_direction == "decreasing"
    assert report.dataset.longitude_direction == "increasing"
    assert report.dataset.variable_mappings["2m_temperature"] == "t2m"
    assert len(report.dataset.variables) == 5
    temperature = report.dataset.variables[0]
    assert temperature.total_count == 696 * 5 * 6
    assert temperature.finite_ratio == 1.0
    assert temperature.units == "K"


def test_file_that_cannot_be_opened_fails_clearly(tmp_path: Path) -> None:
    netcdf = tmp_path / "broken.nc"
    netcdf.write_bytes(b"not a NetCDF file")
    manifest = write_manifest(tmp_path / "manifest.json", netcdf)

    report = validate_era5_file(netcdf, manifest)

    assert report.status == "failed"
    assert "netcdf_open_failed" in _codes(report)


def test_requested_variable_missing_fails(tmp_path: Path) -> None:
    report = _validate(tmp_path, omitted_variables=frozenset({"d2m"}))

    assert "requested_variable_missing" in _codes(report)
    assert report.status == "failed"


def test_unknown_requested_variable_mapping_fails(tmp_path: Path) -> None:
    netcdf = write_era5_netcdf(tmp_path / "sample.nc")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        netcdf,
        variables=("unknown_weather_variable",),
    )

    report = validate_era5_file(netcdf, manifest)

    assert "unknown_requested_variable" in _codes(report)


def test_time_gap_fails_hourly_and_month_count_checks(tmp_path: Path) -> None:
    times = np.delete(complete_month_times(), 100)
    report = _validate(tmp_path, times=times)

    assert {"time_not_hourly", "time_count_mismatch"} <= _codes(report)


def test_duplicate_time_fails(tmp_path: Path) -> None:
    times = complete_month_times().copy()
    times[100] = times[99]
    report = _validate(tmp_path, times=times)

    assert {"time_duplicate", "time_not_monotonic"} <= _codes(report)


def test_incomplete_month_fails_expected_hour_count(tmp_path: Path) -> None:
    report = _validate(tmp_path, times=complete_month_times()[:-1])

    assert {"time_count_mismatch", "time_range_mismatch"} <= _codes(report)


def test_latitude_out_of_legal_range_fails(tmp_path: Path) -> None:
    latitudes = np.array([91.0, 90.75], dtype=np.float64)
    report = _validate(tmp_path, latitudes=latitudes)

    assert "latitude_out_of_range" in _codes(report)


def test_requested_region_not_covered_fails(tmp_path: Path) -> None:
    longitudes = np.array([121.0, 121.25, 121.5], dtype=np.float64)
    report = _validate(tmp_path, longitudes=longitudes)

    assert "requested_area_not_covered" in _codes(report)


def test_unit_conflict_is_error(tmp_path: Path) -> None:
    report = _validate(tmp_path, unit_overrides={"t2m": "degree_Celsius"})

    assert "variable_units_mismatch" in _codes(report)
    assert report.status == "failed"


def test_missing_unit_is_warning_and_exit_status_remains_passing(tmp_path: Path) -> None:
    report = _validate(tmp_path, unit_overrides={"t2m": None})

    assert "variable_units_missing" in _codes(report)
    assert report.status == "passed_with_warnings"
    assert report.error_count == 0


def test_variable_dimension_mismatch_fails(tmp_path: Path) -> None:
    report = _validate(tmp_path, dimension_mismatch_variable="t2m")

    assert {
        "variable_missing_longitude_dimension",
        "variable_unexpected_dimensions",
    } <= _codes(report)


def test_all_nan_variable_fails(tmp_path: Path) -> None:
    report = _validate(tmp_path, all_nan_variable="t2m")

    assert "variable_all_non_finite" in _codes(report)
    assert report.dataset is not None
    temperature = next(
        variable for variable in report.dataset.variables if variable.internal_name == "t2m"
    )
    assert temperature.missing_ratio == 1.0
    assert temperature.finite_ratio == 0.0


def test_infinite_value_fails_and_is_counted(tmp_path: Path) -> None:
    report = _validate(tmp_path, infinite_variable="u10")

    assert "variable_infinite_values" in _codes(report)
    assert report.dataset is not None
    wind = next(
        variable for variable in report.dataset.variables if variable.internal_name == "u10"
    )
    assert wind.non_finite_count == 1


def test_broad_physical_integrity_range_is_enforced(tmp_path: Path) -> None:
    report = _validate(tmp_path, value_overrides={"sp": -1.0})

    assert "variable_below_safe_range" in _codes(report)


def test_manifest_sha256_mismatch_fails(tmp_path: Path) -> None:
    netcdf = write_era5_netcdf(tmp_path / "sample.nc")
    manifest = write_manifest(tmp_path / "manifest.json", netcdf, sha256="0" * 64)

    report = validate_era5_file(netcdf, manifest)

    assert "manifest_sha256_mismatch" in _codes(report)


def test_manifest_file_size_mismatch_fails(tmp_path: Path) -> None:
    netcdf = write_era5_netcdf(tmp_path / "sample.nc")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        netcdf,
        file_size_bytes=netcdf.stat().st_size + 1,
    )

    report = validate_era5_file(netcdf, manifest)

    assert "manifest_size_mismatch" in _codes(report)


def test_missing_manifest_fails_clearly(tmp_path: Path) -> None:
    netcdf = write_era5_netcdf(tmp_path / "sample.nc")

    report = validate_era5_file(netcdf, tmp_path / "missing.json")

    assert "manifest_missing" in _codes(report)


def test_original_netcdf_hash_is_unchanged(tmp_path: Path) -> None:
    netcdf = write_era5_netcdf(tmp_path / "sample.nc")
    manifest = write_manifest(tmp_path / "manifest.json", netcdf)
    before = hashlib.sha256(netcdf.read_bytes()).hexdigest()

    report = validate_era5_file(netcdf, manifest)

    after = hashlib.sha256(netcdf.read_bytes()).hexdigest()
    assert report.status == "passed"
    assert before == after


def test_alternative_explicit_time_coordinate_name_is_supported(tmp_path: Path) -> None:
    report = _validate(tmp_path, time_coordinate_name="time")

    assert report.status == "passed"
    assert report.dataset is not None
    assert report.dataset.time_coordinate == "time"


def test_unsigned_longitude_axis_can_cover_signed_request(tmp_path: Path) -> None:
    longitudes = np.array([0.0, 90.0, 180.0, 270.0], dtype=np.float64)
    netcdf = write_era5_netcdf(tmp_path / "sample.nc", longitudes=longitudes)
    manifest = write_manifest(
        tmp_path / "manifest.json",
        netcdf,
        area=(31.75, -1.0, 30.75, 1.0),
    )

    report = validate_era5_file(netcdf, manifest)

    assert "requested_area_not_covered" not in _codes(report)
    assert "longitude_out_of_range" not in _codes(report)


@pytest.mark.parametrize("variable", REQUESTED_VARIABLES)
def test_every_supported_request_variable_has_an_internal_mapping(
    tmp_path: Path, variable: str
) -> None:
    report = _validate(tmp_path)

    assert report.dataset is not None
    assert report.dataset.variable_mappings[variable] is not None
