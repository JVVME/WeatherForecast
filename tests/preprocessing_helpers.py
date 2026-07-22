"""Offline miniature ERA5 inputs and provenance for M2-A tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import xarray as xr
import yaml

from weather_ai.data.variables import SUPPORTED_PREPROCESSING_VARIABLES

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
    "v10": "m/s",
}


@dataclass(frozen=True, slots=True)
class PreprocessingCase:
    config: Path
    source: Path
    download_manifest: Path
    validation_report: Path
    output_directory: Path


def write_preprocessing_case(
    root: Path,
    *,
    unit_overrides: Mapping[str, str | None] | None = None,
    value_overrides: Mapping[str, float] | None = None,
    omitted_variables: frozenset[str] = frozenset(),
    add_unknown_variable: bool = False,
    nan_variable: str | None = None,
    infinite_variable: str | None = None,
    validation_status: str = "passed",
    validation_hash: str | None = None,
    allow_overwrite: bool = False,
) -> PreprocessingCase:
    """Create one tiny raw file, M1-A manifest, M1-B report, and M2-A config."""

    root.mkdir(parents=True, exist_ok=True)
    source = root / "raw" / "sample.nc"
    source.parent.mkdir(parents=True, exist_ok=True)
    _write_raw(
        source,
        unit_overrides=unit_overrides,
        value_overrides=value_overrides,
        omitted_variables=omitted_variables,
        add_unknown_variable=add_unknown_variable,
        nan_variable=nan_variable,
        infinite_variable=infinite_variable,
    )
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()

    download_manifest = root / "manifests" / "downloads.json"
    download_manifest.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "1.0",
        "dataset": "reanalysis-era5-single-levels",
        "download_status": "success",
        "final_file_path": str(source.resolve()),
        "file_size_bytes": len(payload),
        "sha256": digest,
        "variables": list(SUPPORTED_PREPROCESSING_VARIABLES),
        "area": {
            "id": "test-region",
            "north": 31.75,
            "west": 120.75,
            "south": 30.75,
            "east": 121.0,
        },
        "request_parameters": {
            "year": ["2024"],
            "month": ["01"],
            "variable": list(SUPPORTED_PREPROCESSING_VARIABLES),
        },
    }
    download_manifest.write_text(json.dumps([record]), encoding="utf-8")

    issues: list[dict[str, object]] = []
    error_count = 0
    warning_count = 0
    if validation_status == "failed":
        issues.append(
            {"severity": "error", "code": "fixture_failure", "message": "failed", "variable": None}
        )
        error_count = 1
    elif validation_status == "passed_with_warnings":
        issues.append(
            {
                "severity": "warning",
                "code": "fixture_warning",
                "message": "reviewed warning",
                "variable": None,
            }
        )
        warning_count = 1
    validation_report = root / "validation" / "report.json"
    validation_report.parent.mkdir(parents=True, exist_ok=True)
    reported_hash = validation_hash or digest
    report = {
        "file_path": str(source.resolve()),
        "manifest_path": str(download_manifest.resolve()),
        "file_size_bytes": len(payload),
        "file_sha256": reported_hash,
        "file_sha256_after_validation": reported_hash,
        "status": validation_status,
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issues,
        "dataset": {},
    }
    validation_report.write_text(json.dumps(report), encoding="utf-8")

    output_directory = root / "interim" / "era5"
    config = root / "preprocess.yaml"
    config_payload = {
        "source": {
            "file": str(source.resolve()),
            "download_manifest": str(download_manifest.resolve()),
            "validation_report": str(validation_report.resolve()),
        },
        "output": {
            "directory": str(output_directory.resolve()),
            "format": "netcdf4",
            "allow_overwrite": allow_overwrite,
        },
        "expected_variables": list(SUPPORTED_PREPROCESSING_VARIABLES),
        "dtype": "float32",
        "coordinate_sort": "ascending",
    }
    config.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")
    return PreprocessingCase(
        config=config,
        source=source,
        download_manifest=download_manifest,
        validation_report=validation_report,
        output_directory=output_directory,
    )


def _write_raw(
    path: Path,
    *,
    unit_overrides: Mapping[str, str | None] | None,
    value_overrides: Mapping[str, float] | None,
    omitted_variables: frozenset[str],
    add_unknown_variable: bool,
    nan_variable: str | None,
    infinite_variable: str | None,
) -> None:
    times = np.array(
        ["2024-01-01T00:00", "2024-01-01T01:00", "2024-01-01T02:00"],
        dtype="datetime64[m]",
    )
    latitudes = np.array([31.75, 30.75], dtype=np.float64)
    longitudes = np.array([120.75, 121.0], dtype=np.float64)
    units: dict[str, str | None] = dict(DEFAULT_UNITS)
    if unit_overrides is not None:
        units.update(unit_overrides)
    values = {"t2m": 280.0, "d2m": 275.0, "sp": 100_000.0, "u10": 3.0, "v10": -1.0}
    if value_overrides is not None:
        values.update(value_overrides)

    data_vars: dict[str, xr.DataArray] = {}
    shape = (times.size, latitudes.size, longitudes.size)
    for internal_name in INTERNAL_NAMES.values():
        if internal_name in omitted_variables:
            continue
        array = np.full(shape, values[internal_name], dtype=np.float32)
        if internal_name == "t2m" and value_overrides is None:
            array[:, 0, :] = 281.0
            array[:, 1, :] = 271.0
        if internal_name == nan_variable:
            array[0, 0, 0] = np.nan
        if internal_name == infinite_variable:
            array[0, 0, 0] = np.inf
        attrs = {} if units[internal_name] is None else {"units": units[internal_name]}
        data_vars[internal_name] = xr.DataArray(
            array,
            dims=("valid_time", "latitude", "longitude"),
            attrs=attrs,
        )
    if add_unknown_variable:
        data_vars["tcc"] = xr.DataArray(
            np.zeros(shape, dtype=np.float32),
            dims=("valid_time", "latitude", "longitude"),
            attrs={"units": "1"},
        )

    dataset = xr.Dataset(
        data_vars=data_vars,
        coords={
            "valid_time": times,
            "latitude": latitudes,
            "longitude": longitudes,
            "number": 0,
        },
    )
    try:
        dataset.to_netcdf(path, engine="h5netcdf")
    finally:
        dataset.close()
