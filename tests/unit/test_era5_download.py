from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from weather_ai.cli import main
from weather_ai.data.config import Era5DownloadConfig, load_era5_download_config
from weather_ai.data.errors import DataDownloadError, TargetExistsError
from weather_ai.data.service import DownloadPlan, execute_download, plan_download


class RecordingClient:
    def __init__(self, payload: bytes = b"fake-netcdf") -> None:
        self.payload = payload
        self.calls = 0

    def download(self, dataset: str, request: dict[str, object], target: Path) -> None:
        self.calls += 1
        target.write_bytes(self.payload)


class FailingClient:
    def __init__(self, secret: str = "not-used") -> None:
        self.secret = secret
        self.calls = 0

    def download(self, dataset: str, request: dict[str, object], target: Path) -> None:
        self.calls += 1
        target.write_bytes(b"partial")
        raise RuntimeError(f"remote failure with key={self.secret}")


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "era5.yaml"
    config_path.write_text(
        f"""
scope: sample
dataset: reanalysis-era5-single-levels
year: 2024
month: 2
area: {{id: test-area, north: 1, west: -1, south: -1, east: 1}}
variables:
  - 2m_temperature
  - surface_pressure
output:
  format: netcdf
  directory: {tmp_path.as_posix()}/raw
  manifest: {tmp_path.as_posix()}/manifests/downloads.json
logging: {{level: INFO, format: json}}
""".strip(),
        encoding="utf-8",
    )
    return config_path


def _load_plan(tmp_path: Path) -> tuple[Era5DownloadConfig, DownloadPlan]:
    config = load_era5_download_config(_write_config(tmp_path))
    return config, plan_download(config, working_directory=tmp_path)


def test_dry_run_does_not_call_client_or_create_output_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _write_config(tmp_path)
    client = RecordingClient()

    exit_code = main(
        ["data", "download", "--config", str(config_path), "--dry-run"],
        download_client=client,
    )

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert exit_code == 0
    assert client.calls == 0
    assert result["mode"] == "dry-run"
    assert result["request_parameters"]["year"] == ["2024"]
    assert result["manifest_preview"]["schema_version"] == "1.0"
    assert result["manifest_preview"]["data_time_end"] == "2024-02-29T23:00:00Z"
    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "manifests").exists()


def test_existing_target_refuses_overwrite_before_client_call(tmp_path: Path) -> None:
    config, plan = _load_plan(tmp_path)
    plan.paths.final.parent.mkdir(parents=True)
    plan.paths.final.write_bytes(b"original")
    client = RecordingClient()

    with pytest.raises(TargetExistsError, match="refusing to overwrite"):
        execute_download(
            config,
            plan,
            client,
            project_version="0.1.0",
            git_commit=None,
        )

    assert client.calls == 0
    assert plan.paths.final.read_bytes() == b"original"


def test_failed_download_creates_no_final_file_and_cleans_partial(tmp_path: Path) -> None:
    config, plan = _load_plan(tmp_path)

    with pytest.raises(DataDownloadError, match="no final file was created"):
        execute_download(
            config,
            plan,
            FailingClient(),
            project_version="0.1.0",
            git_commit=None,
        )

    assert not plan.paths.final.exists()
    assert not plan.paths.temporary.exists()
    assert not plan.manifest_path.exists()


def test_success_atomically_moves_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, plan = _load_plan(tmp_path)
    original_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def tracking_replace(source: str | Path, destination: str | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr("weather_ai.data.files.os.replace", tracking_replace)

    execute_download(
        config,
        plan,
        RecordingClient(b"complete"),
        project_version="0.1.0",
        git_commit=None,
    )

    assert (plan.paths.temporary, plan.paths.final) in replacements
    assert plan.paths.final.read_bytes() == b"complete"
    assert not plan.paths.temporary.exists()


def test_manifest_contains_size_sha256_and_provenance(tmp_path: Path) -> None:
    config, plan = _load_plan(tmp_path)
    payload = b"verified fake NetCDF bytes"
    requested_at = datetime(2026, 7, 22, 12, 30, tzinfo=UTC)

    record = execute_download(
        config,
        plan,
        RecordingClient(payload),
        project_version="0.1.0",
        git_commit="abc123",
        requested_at=requested_at,
    )

    manifest = json.loads(plan.manifest_path.read_text(encoding="utf-8"))
    assert len(manifest) == 1
    assert manifest[0] == record.as_dict()
    assert manifest[0]["file_size_bytes"] == len(payload)
    assert manifest[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert manifest[0]["requested_at"] == requested_at.isoformat()
    assert manifest[0]["git_commit"] == "abc123"
    assert manifest[0]["download_status"] == "success"


def test_client_secret_is_not_written_to_cli_output_or_logs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "SUPER-SECRET-CDS-TOKEN"

    exit_code = main(
        ["data", "download", "--config", str(_write_config(tmp_path))],
        download_client=FailingClient(secret),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert secret not in captured.out
    assert secret not in captured.err
    assert "CDS download failed" in captured.err
