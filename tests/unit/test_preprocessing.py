from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import xarray as xr
import yaml

from tests.preprocessing_helpers import PreprocessingCase, write_preprocessing_case
from weather_ai.config import ConfigError
from weather_ai.data.errors import (
    PreprocessingError,
    PreprocessingPreconditionError,
    PreprocessingValidationError,
)
from weather_ai.data.preprocessing import (
    PreprocessingPlan,
    execute_preprocessing,
    plan_preprocessing,
)
from weather_ai.data.preprocessing_config import (
    Era5PreprocessingConfig,
    load_era5_preprocessing_config,
)


def _load(case: PreprocessingCase) -> tuple[Era5PreprocessingConfig, PreprocessingPlan]:
    config = load_era5_preprocessing_config(case.config)
    return config, plan_preprocessing(config)


def _execute(case: PreprocessingCase):  # type: ignore[no-untyped-def]
    config, plan = _load(case)
    manifest = execute_preprocessing(
        config,
        plan,
        software_version="0.1.0",
        git_commit="abc123",
    )
    return plan, manifest


def test_success_normalizes_values_names_dimensions_and_coordinates(tmp_path: Path) -> None:
    case = write_preprocessing_case(tmp_path)
    source_hash_before = hashlib.sha256(case.source.read_bytes()).hexdigest()

    plan, manifest = _execute(case)

    assert plan.paths.output.is_file()
    assert plan.paths.manifest.is_file()
    assert manifest.status == "success"
    assert manifest.output_variables == (
        "temperature_2m",
        "dewpoint_temperature_2m",
        "surface_pressure",
        "wind_u_10m",
        "wind_v_10m",
    )
    assert manifest.coordinate_normalization.sorted_coordinates == ("latitude",)
    assert manifest.dimension_order == ("time", "latitude", "longitude")
    assert hashlib.sha256(case.source.read_bytes()).hexdigest() == source_hash_before

    with xr.open_dataset(plan.paths.output, engine="h5netcdf") as dataset:
        assert tuple(dataset.data_vars) == manifest.output_variables
        assert tuple(dataset["temperature_2m"].dims) == ("time", "latitude", "longitude")
        assert dataset["temperature_2m"].dtype == np.dtype("float32")
        assert dataset["temperature_2m"].attrs["units"] == "degC"
        assert dataset["dewpoint_temperature_2m"].attrs["units"] == "degC"
        assert dataset["surface_pressure"].attrs["units"] == "hPa"
        assert dataset["wind_u_10m"].attrs["units"] == "m s-1"
        np.testing.assert_allclose(dataset["latitude"].values, [30.75, 31.75])
        np.testing.assert_allclose(dataset["longitude"].values, [120.75, 121.0])
        np.testing.assert_allclose(
            dataset["temperature_2m"].isel(time=0, longitude=0).values,
            [-2.15, 7.85],
            atol=1e-5,
        )
        np.testing.assert_allclose(dataset["dewpoint_temperature_2m"].values, 1.85, atol=1e-5)
        np.testing.assert_allclose(dataset["surface_pressure"].values, 1000.0)
        np.testing.assert_allclose(dataset["wind_u_10m"].values, 3.0)
        np.testing.assert_allclose(dataset["wind_v_10m"].values, -1.0)


def test_manifest_records_exact_hashes_statistics_and_provenance(tmp_path: Path) -> None:
    case = write_preprocessing_case(tmp_path, validation_status="passed_with_warnings")

    plan, manifest = _execute(case)
    stored = json.loads(plan.paths.manifest.read_text(encoding="utf-8"))

    assert stored == manifest.as_dict()
    assert stored["source_file_sha256"] == hashlib.sha256(case.source.read_bytes()).hexdigest()
    output_hash = hashlib.sha256(plan.paths.output.read_bytes()).hexdigest()
    assert stored["output_file_sha256"] == output_hash
    assert stored["output_file_size"] == plan.paths.output.stat().st_size
    assert stored["source_validation_status"] == "passed_with_warnings"
    assert stored["source_validation_warnings"][0]["code"] == "fixture_warning"
    assert stored["variables"][0]["shape"] == [3, 2, 2]
    assert stored["variables"][0]["nan_count"] == 0


def test_nan_is_preserved_and_counted_but_infinity_is_rejected(tmp_path: Path) -> None:
    nan_case = write_preprocessing_case(tmp_path / "nan", nan_variable="t2m")

    _, manifest = _execute(nan_case)

    temperature = next(item for item in manifest.variables if item.name == "temperature_2m")
    assert temperature.nan_count == 1
    assert temperature.non_finite_count == 1

    infinite_case = write_preprocessing_case(tmp_path / "inf", infinite_variable="u10")
    config, plan = _load(infinite_case)
    with pytest.raises(PreprocessingValidationError, match="infinite"):
        execute_preprocessing(config, plan, software_version="test", git_commit=None)
    assert not plan.paths.output.exists()
    assert not plan.paths.manifest.exists()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"unit_overrides": {"t2m": "degC"}}, "units"),
        ({"unit_overrides": {"sp": None}}, "no units"),
        ({"omitted_variables": frozenset({"d2m"})}, "missing required"),
        ({"add_unknown_variable": True}, "unknown or unrequested"),
        ({"value_overrides": {"t2m": 500.0}}, "maximum"),
        ({"value_overrides": {"sp": 20_000.0}}, "minimum"),
        ({"value_overrides": {"u10": 151.0}}, "maximum"),
    ],
)
def test_invalid_raw_contract_fails_without_publishing(
    tmp_path: Path,
    options: dict[str, Any],
    message: str,
) -> None:
    case = write_preprocessing_case(tmp_path, **options)
    config, plan = _load(case)

    with pytest.raises(PreprocessingValidationError, match=message):
        execute_preprocessing(config, plan, software_version="test", git_commit=None)

    assert not plan.paths.output.exists()
    assert not plan.paths.manifest.exists()
    assert not plan.paths.temporary_output.exists()


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_m1b_nonpassing_status_is_rejected(tmp_path: Path, status: str) -> None:
    case = write_preprocessing_case(tmp_path, validation_status=status)
    config = load_era5_preprocessing_config(case.config)

    with pytest.raises(PreprocessingPreconditionError, match="validation"):
        plan_preprocessing(config)


def test_m1b_hash_or_path_mismatch_is_rejected(tmp_path: Path) -> None:
    hash_case = write_preprocessing_case(tmp_path / "hash", validation_hash="0" * 64)
    hash_config = load_era5_preprocessing_config(hash_case.config)
    with pytest.raises(PreprocessingPreconditionError, match="SHA-256"):
        plan_preprocessing(hash_config)

    path_case = write_preprocessing_case(tmp_path / "path")
    report = json.loads(path_case.validation_report.read_text(encoding="utf-8"))
    report["file_path"] = str((tmp_path / "other.nc").resolve())
    path_case.validation_report.write_text(json.dumps(report), encoding="utf-8")
    path_config = load_era5_preprocessing_config(path_case.config)
    with pytest.raises(PreprocessingPreconditionError, match="file_path"):
        plan_preprocessing(path_config)


def test_download_manifest_mismatch_is_rejected(tmp_path: Path) -> None:
    case = write_preprocessing_case(tmp_path)
    records = json.loads(case.download_manifest.read_text(encoding="utf-8"))
    records[0]["sha256"] = "0" * 64
    case.download_manifest.write_text(json.dumps(records), encoding="utf-8")
    config = load_era5_preprocessing_config(case.config)

    with pytest.raises(PreprocessingPreconditionError, match="download manifest SHA-256"):
        plan_preprocessing(config)


def test_existing_output_is_not_overwritten_by_default(tmp_path: Path) -> None:
    case = write_preprocessing_case(tmp_path)
    plan, _ = _execute(case)
    original = plan.paths.output.read_bytes()
    config = load_era5_preprocessing_config(case.config)

    with pytest.raises(PreprocessingError, match="overwrite"):
        execute_preprocessing(
            config,
            plan_preprocessing(config),
            software_version="test",
            git_commit=None,
        )

    assert plan.paths.output.read_bytes() == original


def test_write_failure_leaves_no_final_output_or_success_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = write_preprocessing_case(tmp_path)
    config, plan = _load(case)

    def fail_write(*args: object, **kwargs: object) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(xr.Dataset, "to_netcdf", fail_write)
    with pytest.raises(OSError, match="simulated"):
        execute_preprocessing(config, plan, software_version="test", git_commit=None)

    assert not plan.paths.output.exists()
    assert not plan.paths.manifest.exists()
    assert not plan.paths.temporary_output.exists()


def test_dry_run_plan_creates_no_output(tmp_path: Path) -> None:
    case = write_preprocessing_case(tmp_path)
    config, plan = _load(case)

    payload = plan.as_dry_run_dict(config)

    assert payload["status"] == "dry_run"
    assert payload["writes_performed"] is False
    assert payload["dimension_order"] == ["time", "latitude", "longitude"]
    assert not case.output_directory.exists()
    assert not plan.paths.output.exists()
    assert not plan.paths.manifest.exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw.update({"unexpected": True}), "unknown key"),
        (lambda raw: raw.update({"dtype": "int16"}), "dtype"),
        (lambda raw: raw.update({"coordinate_sort": "automatic"}), "coordinate_sort"),
        (lambda raw: raw["output"].update({"format": "zarr"}), "output.format"),
        (lambda raw: raw.pop("expected_variables"), "missing required key"),
    ],
)
def test_preprocessing_config_is_strict(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    case = write_preprocessing_case(tmp_path)
    raw = yaml.safe_load(case.config.read_text(encoding="utf-8"))
    mutation(raw)
    case.config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_era5_preprocessing_config(case.config)


def test_preprocessing_config_rejects_missing_evidence_path(tmp_path: Path) -> None:
    case = write_preprocessing_case(tmp_path)
    case.validation_report.unlink()

    with pytest.raises(ConfigError, match="does not exist"):
        load_era5_preprocessing_config(case.config)
