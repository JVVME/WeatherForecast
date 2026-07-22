"""Best-effort source provenance that does not block data downloads."""

from __future__ import annotations

import subprocess
from pathlib import Path


def get_git_commit(working_directory: Path | None = None) -> str | None:
    """Return the current Git commit, or ``None`` outside an accessible repository."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=working_directory,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        return None
    commit = result.stdout.strip()
    return commit or None
