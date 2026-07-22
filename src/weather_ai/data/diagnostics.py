"""Safe, structured diagnostics for failed ERA5 downloads."""

from __future__ import annotations

import re
from dataclasses import dataclass

_REDACTED = "[REDACTED]"
_CDSAPIRC_CONTENT_PATTERN = re.compile(
    r"(?is)(?:contents?\s+of\s+\.cdsapirc|\.cdsapirc\s+contents?)\s*[:=].*"
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?im)(\b(?:proxy-)?authorization\s*:\s*)(?:bearer\s+)?[^\r\n]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)([\"']?(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|token|"
    r"authorization|password|secret|key)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]\r\n]+)"
)
_JWT_PATTERN = re.compile(
    r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b"
)


@dataclass(frozen=True, slots=True)
class ExceptionDiagnostic:
    """Sanitized type and text for one exception in a cause chain."""

    type: str
    message: str

    def as_dict(self) -> dict[str, str]:
        """Return JSON-compatible values."""

        return {"type": self.type, "message": self.message}


@dataclass(frozen=True, slots=True)
class DownloadFailureDiagnostics:
    """Whitelisted verbose diagnostics that contain no raw credential fields."""

    dataset: str
    request_summary: dict[str, object]
    root_cause: ExceptionDiagnostic
    exception_chain: tuple[ExceptionDiagnostic, ...]

    def as_dict(self) -> dict[str, object]:
        """Return the stable verbose diagnostic schema."""

        return {
            "dataset": self.dataset,
            "request_summary": self.request_summary,
            "root_cause": self.root_cause.as_dict(),
            "exception_chain": [item.as_dict() for item in self.exception_chain],
        }


def sanitize_sensitive_text(text: str) -> str:
    """Redact credential-shaped text without reading credential files.

    The sanitizer handles Authorization headers, Bearer values, common key/token
    assignments, JWTs, and exception text that explicitly embeds ``.cdsapirc`` contents.
    """

    sanitized = _CDSAPIRC_CONTENT_PATTERN.sub(f".cdsapirc contents: {_REDACTED}", text)
    sanitized = _AUTHORIZATION_PATTERN.sub(rf"\1{_REDACTED}", sanitized)
    sanitized = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", sanitized)
    sanitized = _SENSITIVE_ASSIGNMENT_PATTERN.sub(rf"\1{_REDACTED}", sanitized)
    return _JWT_PATTERN.sub(_REDACTED, sanitized)


def build_download_failure_diagnostics(
    error: BaseException,
    *,
    dataset: str,
    request: dict[str, object],
) -> DownloadFailureDiagnostics:
    """Build safe diagnostics from an exception cause chain and request whitelist."""

    chain = _build_exception_chain(error)
    return DownloadFailureDiagnostics(
        dataset=sanitize_sensitive_text(dataset),
        request_summary=_summarize_request(request),
        root_cause=chain[-1],
        exception_chain=chain,
    )


def _build_exception_chain(error: BaseException) -> tuple[ExceptionDiagnostic, ...]:
    result: list[ExceptionDiagnostic] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        result.append(
            ExceptionDiagnostic(
                type=sanitize_sensitive_text(type(current).__name__),
                message=sanitize_sensitive_text(str(current)),
            )
        )
        current = current.__cause__
    return tuple(result)


def _summarize_request(request: dict[str, object]) -> dict[str, object]:
    return {
        "year": _safe_value(request.get("year")),
        "month": _safe_value(request.get("month")),
        "day_count": _sequence_length(request.get("day")),
        "time_count": _sequence_length(request.get("time")),
        "variables": _safe_value(request.get("variable")),
        "area": _safe_value(request.get("area")),
        "product_type": _safe_value(request.get("product_type")),
        "data_format": _safe_value(request.get("data_format")),
        "download_format": _safe_value(request.get("download_format")),
    }


def _sequence_length(value: object) -> int | None:
    return len(value) if isinstance(value, list | tuple) else None


def _safe_value(value: object) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return sanitize_sensitive_text(value)
    if isinstance(value, list | tuple):
        return [_safe_value(item) for item in value]
    return sanitize_sensitive_text(str(value))
