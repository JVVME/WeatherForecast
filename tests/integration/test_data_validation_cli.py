from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.data_validation_helpers import write_era5_netcdf, write_manifest
from weather_ai.cli import main


def test_netcdf_manifest_cli_to_json_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    netcdf = write_era5_netcdf(tmp_path / "sample.nc")
    manifest = write_manifest(tmp_path / "manifest.json", netcdf)
    report_path = tmp_path / "artifacts" / "validation.json"
    before = hashlib.sha256(netcdf.read_bytes()).hexdigest()

    exit_code = main(
        [
            "data",
            "validate",
            "--file",
            str(netcdf),
            "--manifest",
            str(manifest),
            "--output-json",
            str(report_path),
        ]
    )

    captured = capsys.readouterr()
    stdout_report = json.loads(captured.out)
    file_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert stdout_report == file_report
    assert file_report["status"] == "passed"
    assert file_report["dataset"]["variable_mappings"]["surface_pressure"] == "sp"
    assert hashlib.sha256(netcdf.read_bytes()).hexdigest() == before


def test_cli_returns_nonzero_for_validation_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    netcdf = write_era5_netcdf(tmp_path / "sample.nc", omitted_variables=frozenset({"sp"}))
    manifest = write_manifest(tmp_path / "manifest.json", netcdf)

    exit_code = main(
        [
            "data",
            "validate",
            "--file",
            str(netcdf),
            "--manifest",
            str(manifest),
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code != 0
    assert report["status"] == "failed"


def test_validate_help_lists_required_inputs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["data", "validate", "--help"])

    output = capsys.readouterr().out
    assert exit_info.value.code == 0
    assert "--file" in output
    assert "--manifest" in output
    assert "--output-json" in output
