"""Pure construction of normalized CDS requests and stable target names."""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from pathlib import Path

from weather_ai.data.config import Era5DownloadConfig


def build_era5_request(config: Era5DownloadConfig) -> dict[str, object]:
    """Build a normalized CDS API request for one complete calendar month."""

    last_day = calendar.monthrange(config.year, config.month)[1]
    return {
        "area": config.area.as_cds_list(),
        "data_format": config.output.format,
        "day": [f"{day:02d}" for day in range(1, last_day + 1)],
        "download_format": "unarchived",
        "month": [f"{config.month:02d}"],
        "product_type": ["reanalysis"],
        "time": [f"{hour:02d}:00" for hour in range(24)],
        "variable": list(config.variables),
        "year": [str(config.year)],
    }


def build_target_path(
    config: Era5DownloadConfig,
    request: dict[str, object],
    *,
    working_directory: Path | None = None,
) -> Path:
    """Return an absolute, deterministic final path for the normalized request."""

    root = working_directory if working_directory is not None else Path.cwd()
    output_directory = config.output.directory.expanduser()
    if not output_directory.is_absolute():
        output_directory = root / output_directory

    identity = {"dataset": config.dataset, "request": request}
    canonical = json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    dataset_slug = re.sub(r"[^a-z0-9]+", "-", config.dataset.lower()).strip("-")
    filename = (
        f"{dataset_slug}_{config.year:04d}-{config.month:02d}_"
        f"{config.area.identifier}_{request_hash}.nc"
    )
    return (output_directory / filename).resolve()


def resolve_manifest_path(
    config: Era5DownloadConfig, *, working_directory: Path | None = None
) -> Path:
    """Resolve the configured manifest path without touching the filesystem."""

    root = working_directory if working_directory is not None else Path.cwd()
    manifest_path = config.output.manifest.expanduser()
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    return manifest_path.resolve()
