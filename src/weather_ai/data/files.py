"""Validation and atomic publication of raw download files."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from weather_ai.data.errors import DownloadValidationError, TargetExistsError


@dataclass(frozen=True, slots=True)
class DownloadPaths:
    """Final and same-directory temporary paths for one download."""

    final: Path
    temporary: Path


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    """Validated metadata for a published raw file."""

    path: Path
    size_bytes: int
    sha256: str


def make_download_paths(final_path: Path) -> DownloadPaths:
    """Place the temporary file beside the final file for atomic replacement."""

    return DownloadPaths(
        final=final_path,
        temporary=final_path.with_suffix(final_path.suffix + ".part"),
    )


class DownloadFileManager:
    """Own temporary-file lifecycle and publish a validated file atomically."""

    def prepare(self, paths: DownloadPaths) -> None:
        """Refuse collisions before any network call, then create the target directory."""

        self._ensure_available(paths)
        paths.final.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_available(paths)

    def finalize(self, paths: DownloadPaths) -> DownloadedFile:
        """Validate the temporary file and atomically rename it to the final path."""

        size_bytes = validate_nonempty_file(paths.temporary)
        digest = sha256_file(paths.temporary)
        if paths.final.exists():
            raise TargetExistsError(f"refusing to overwrite existing target: {paths.final}")

        # Both paths share a directory, so os.replace is an atomic rename on supported local
        # filesystems. M1-A intentionally does not support concurrent downloads of one target.
        os.replace(paths.temporary, paths.final)
        return DownloadedFile(path=paths.final, size_bytes=size_bytes, sha256=digest)

    def cleanup_temporary(self, paths: DownloadPaths) -> None:
        """Remove only this operation's known partial file, if the client created it."""

        paths.temporary.unlink(missing_ok=True)

    @staticmethod
    def _ensure_available(paths: DownloadPaths) -> None:
        if paths.final.exists():
            raise TargetExistsError(f"refusing to overwrite existing target: {paths.final}")
        if paths.temporary.exists():
            raise TargetExistsError(
                f"refusing to overwrite existing temporary file: {paths.temporary}"
            )


def validate_nonempty_file(path: Path) -> int:
    """Return file size after verifying that a regular, non-empty file exists."""

    if not path.is_file():
        raise DownloadValidationError(f"download did not create a temporary file: {path}")
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise DownloadValidationError(f"downloaded temporary file is empty: {path}")
    return size_bytes


def sha256_file(path: Path) -> str:
    """Compute SHA-256 without loading the entire file into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
