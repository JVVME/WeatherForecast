from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import xarray as xr
import yaml

from tests.data_validation_helpers import REQUESTED_VARIABLES, write_era5_netcdf, write_manifest
from tests.preprocessing_helpers import write_preprocessing_case
from weather_ai.cli import main


def test_m1b_report_to_preprocess_cli_to_reopened_interim_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = write_era5_netcdf(tmp_path / "raw.nc")
    download_manifest = write_manifest(tmp_path / "downloads.json", source)
    validation_report = tmp_path / "validation.json"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    validation_exit = main(
        [
            "data",
            "validate",
            "--file",
            str(source),
            "--manifest",
            str(download_manifest),
            "--output-json",
            str(validation_report),
        ]
    )
    capsys.readouterr()
    assert validation_exit == 0

    secret = "CDS-TOKEN-MUST-NOT-LEAK"
    records = json.loads(download_manifest.read_text(encoding="utf-8"))
    records[0]["cds_token"] = secret
    download_manifest.write_text(json.dumps(records), encoding="utf-8")
    config = tmp_path / "preprocess.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "source": {
                    "file": str(source.resolve()),
                    "download_manifest": str(download_manifest.resolve()),
                    "validation_report": str(validation_report.resolve()),
                },
                "output": {
                    "directory": str((tmp_path / "interim").resolve()),
                    "format": "netcdf4",
                    "allow_overwrite": False,
                },
                "expected_variables": list(REQUESTED_VARIABLES),
                "dtype": "float32",
                "coordinate_sort": "ascending",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    preprocess_exit = main(["data", "preprocess", "--config", str(config)])

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    output = Path(result["output_file"])
    if not output.is_absolute():
        output = Path.cwd() / output
    assert preprocess_exit == 0
    assert result["status"] == "success"
    assert secret not in captured.out + captured.err
    assert source_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    assert output.is_file()
    with xr.open_dataset(output, engine="h5netcdf") as dataset:
        assert set(dataset.data_vars) == {
            "temperature_2m",
            "dewpoint_temperature_2m",
            "surface_pressure",
            "wind_u_10m",
            "wind_v_10m",
        }
        assert tuple(dataset["temperature_2m"].dims) == (
            "time",
            "latitude",
            "longitude",
        )
        assert dataset["temperature_2m"].attrs["units"] == "degC"
        assert bool((dataset["latitude"].diff("latitude") > 0).all())


def test_preprocess_cli_dry_run_writes_nothing_and_failure_is_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    passing = write_preprocessing_case(tmp_path / "passing")

    dry_run_exit = main(
        ["data", "preprocess", "--config", str(passing.config), "--dry-run"]
    )

    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run_exit == 0
    assert dry_run["status"] == "dry_run"
    assert not passing.output_directory.exists()

    failing = write_preprocessing_case(tmp_path / "failing", validation_status="failed")
    failure_exit = main(["data", "preprocess", "--config", str(failing.config)])

    captured = capsys.readouterr()
    assert failure_exit != 0
    assert "M1-B" in captured.err
    assert not failing.output_directory.exists()


def test_preprocess_help_lists_config_and_dry_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["data", "preprocess", "--help"])

    output = capsys.readouterr().out
    assert exit_info.value.code == 0
    assert "--config" in output
    assert "--dry-run" in output
