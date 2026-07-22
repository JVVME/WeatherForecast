"""Orchestration for dry-run planning and safe ERA5 downloads."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from weather_ai.data.client import DownloadClient
from weather_ai.data.config import Era5DownloadConfig
from weather_ai.data.errors import DataDownloadError
from weather_ai.data.files import (
    DownloadFileManager,
    DownloadPaths,
    make_download_paths,
)
from weather_ai.data.manifest import (
    MANIFEST_SCHEMA_VERSION,
    JsonManifestStore,
    ManifestRecord,
    create_manifest_record,
)
from weather_ai.data.request import (
    build_era5_request,
    build_target_path,
    resolve_manifest_path,
)


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    """Pure, filesystem-independent result of resolving a sample configuration."""

    dataset: str
    request: dict[str, object]
    paths: DownloadPaths
    manifest_path: Path

    def as_dry_run_dict(
        self,
        config: Era5DownloadConfig,
        *,
        project_version: str | None,
        git_commit: str | None,
    ) -> dict[str, object]:
        """Render request, target, and fields that a successful manifest would contain."""

        last_day = calendar.monthrange(config.year, config.month)[1]
        return {
            "mode": "dry-run",
            "dataset": self.dataset,
            "request_parameters": self.request,
            "target_path": str(self.paths.final),
            "temporary_path": str(self.paths.temporary),
            "manifest_path": str(self.manifest_path),
            "manifest_preview": {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "dataset": self.dataset,
                "requested_at": "recorded when an actual request starts",
                "data_time_start": (
                    f"{config.year:04d}-{config.month:02d}-01T00:00:00Z"
                ),
                "data_time_end": (
                    f"{config.year:04d}-{config.month:02d}-{last_day:02d}T23:00:00Z"
                ),
                "variables": list(config.variables),
                "area": config.area.as_dict(),
                "output_format": config.output.format,
                "final_file_path": str(self.paths.final),
                "file_size_bytes": "computed after download",
                "sha256": "computed after download",
                "request_parameters": self.request,
                "project_version": project_version,
                "git_commit": git_commit,
                "download_status": "success only after validation and atomic rename",
            },
        }


def plan_download(
    config: Era5DownloadConfig, *, working_directory: Path | None = None
) -> DownloadPlan:
    """Resolve a normalized request and paths without creating files or clients."""

    request = build_era5_request(config)
    final_path = build_target_path(config, request, working_directory=working_directory)
    return DownloadPlan(
        dataset=config.dataset,
        request=request,
        paths=make_download_paths(final_path),
        manifest_path=resolve_manifest_path(config, working_directory=working_directory),
    )


def execute_download(
    config: Era5DownloadConfig,
    plan: DownloadPlan,
    client: DownloadClient,
    *,
    project_version: str | None,
    git_commit: str | None,
    requested_at: datetime | None = None,
    file_manager: DownloadFileManager | None = None,
    manifest_store: JsonManifestStore | None = None,
) -> ManifestRecord:
    """Download to a temporary path, validate, publish, and record a success manifest."""

    manager = file_manager if file_manager is not None else DownloadFileManager()
    store = manifest_store if manifest_store is not None else JsonManifestStore(plan.manifest_path)
    request_started_at = requested_at if requested_at is not None else datetime.now(UTC)

    manager.prepare(plan.paths)
    try:
        client.download(plan.dataset, plan.request, plan.paths.temporary)
    except Exception as error:
        manager.cleanup_temporary(plan.paths)
        raise DataDownloadError(
            "CDS download failed; no final file was created. Check local credentials, dataset "
            "licence acceptance, and CDS request status."
        ) from error

    try:
        downloaded_file = manager.finalize(plan.paths)
    except (OSError, DataDownloadError):
        manager.cleanup_temporary(plan.paths)
        raise

    record = create_manifest_record(
        config,
        plan.request,
        downloaded_file,
        requested_at=request_started_at,
        project_version=project_version,
        git_commit=git_commit,
    )
    store.append(record)
    return record
