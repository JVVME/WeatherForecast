"""Typed manifest records and atomic JSON manifest storage."""

from __future__ import annotations

import calendar
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from weather_ai.data.config import Era5DownloadConfig
from weather_ai.data.errors import ManifestError
from weather_ai.data.files import DownloadedFile

MANIFEST_SCHEMA_VERSION = "1.0"
DownloadStatus = Literal["success"]


@dataclass(frozen=True, slots=True)
class ManifestRecord:
    """Stable metadata for one successfully validated and published download."""

    schema_version: str
    dataset: str
    requested_at: str
    data_time_start: str
    data_time_end: str
    variables: tuple[str, ...]
    area: dict[str, object]
    output_format: str
    final_file_path: str
    file_size_bytes: int
    sha256: str
    request_parameters: dict[str, object]
    project_version: str | None
    git_commit: str | None
    download_status: DownloadStatus

    def as_dict(self) -> dict[str, object]:
        """Convert to JSON-compatible built-in types with stable field names."""

        return {
            "schema_version": self.schema_version,
            "dataset": self.dataset,
            "requested_at": self.requested_at,
            "data_time_start": self.data_time_start,
            "data_time_end": self.data_time_end,
            "variables": list(self.variables),
            "area": self.area,
            "output_format": self.output_format,
            "final_file_path": self.final_file_path,
            "file_size_bytes": self.file_size_bytes,
            "sha256": self.sha256,
            "request_parameters": self.request_parameters,
            "project_version": self.project_version,
            "git_commit": self.git_commit,
            "download_status": self.download_status,
        }


def create_manifest_record(
    config: Era5DownloadConfig,
    request: dict[str, object],
    downloaded_file: DownloadedFile,
    *,
    requested_at: datetime,
    project_version: str | None,
    git_commit: str | None,
) -> ManifestRecord:
    """Create a success record only after file validation and publication."""

    last_day = calendar.monthrange(config.year, config.month)[1]
    return ManifestRecord(
        schema_version=MANIFEST_SCHEMA_VERSION,
        dataset=config.dataset,
        requested_at=requested_at.isoformat(),
        data_time_start=f"{config.year:04d}-{config.month:02d}-01T00:00:00Z",
        data_time_end=(
            f"{config.year:04d}-{config.month:02d}-{last_day:02d}T23:00:00Z"
        ),
        variables=config.variables,
        area=config.area.as_dict(),
        output_format=config.output.format,
        final_file_path=str(downloaded_file.path),
        file_size_bytes=downloaded_file.size_bytes,
        sha256=downloaded_file.sha256,
        request_parameters=request,
        project_version=project_version,
        git_commit=git_commit,
        download_status="success",
    )


class JsonManifestStore:
    """Append records by atomically replacing one stable JSON document."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: ManifestRecord) -> None:
        """Append a record while preserving any valid existing JSON entries."""

        records = self._read_records()
        records.append(record.as_dict())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        if temporary_path.exists():
            raise ManifestError(f"manifest temporary file already exists: {temporary_path}")

        try:
            with temporary_path.open("x", encoding="utf-8", newline="\n") as file_handle:
                json.dump(records, file_handle, ensure_ascii=False, indent=2, sort_keys=True)
                file_handle.write("\n")
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, TypeError, ValueError) as error:
            temporary_path.unlink(missing_ok=True)
            raise ManifestError(f"could not update manifest: {self.path}") from error

    def _read_records(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ManifestError(f"could not read valid manifest JSON: {self.path}") from error
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise ManifestError(f"manifest root must be a JSON array of objects: {self.path}")
        return [cast(dict[str, object], item) for item in raw]
