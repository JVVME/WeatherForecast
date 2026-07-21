"""Structured logging setup using only the Python standard library."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TextIO


class JsonFormatter(logging.Formatter):
    """Render one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize stable log fields and optional event context."""

        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("event", "config_path"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str, log_format: str, stream: TextIO | None = None) -> None:
    """Configure the process root logger with deterministic handlers.

    Args:
        level: Standard Python log level name, already validated by configuration loading.
        log_format: Either ``json`` for JSON Lines or ``text`` for human-readable output.
        stream: Optional output stream; defaults to standard error.

    Raises:
        ValueError: If ``level`` or ``log_format`` is unsupported.
    """

    numeric_level = logging.getLevelNamesMapping().get(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"unsupported log level: {level}")

    handler = logging.StreamHandler(stream)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    elif log_format == "text":
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    else:
        raise ValueError(f"unsupported log format: {log_format}")

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)
