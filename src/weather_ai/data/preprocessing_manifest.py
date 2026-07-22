"""Atomic storage for one-file M2-A preprocessing manifests."""

from __future__ import annotations

import json
import os
from pathlib import Path

from weather_ai.data.errors import PreprocessingError
from weather_ai.data.preprocessing_schemas import PreprocessingManifest


def write_preprocessing_manifest(
    path: Path,
    manifest: PreprocessingManifest,
    *,
    allow_overwrite: bool,
) -> None:
    """Atomically publish a validated success manifest beside its output file."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        raise PreprocessingError(f"preprocessing manifest temporary path exists: {temporary}")
    if path.exists() and not allow_overwrite:
        raise PreprocessingError(f"refusing to overwrite preprocessing manifest: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as file_handle:
            json.dump(
                manifest.as_dict(),
                file_handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            file_handle.write("\n")
            file_handle.flush()
            os.fsync(file_handle.fileno())
        if path.exists() and not allow_overwrite:
            raise PreprocessingError(f"refusing to overwrite preprocessing manifest: {path}")
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        temporary.unlink(missing_ok=True)
        raise
