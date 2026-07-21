"""Typed loading and validation for WeatherAI YAML configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

LogFormat = Literal["json", "text"]
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigError(ValueError):
    """Raised when a configuration file violates the M0 schema."""


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Project identity and canonical timezone."""

    name: str
    timezone: str


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Application log level and rendering format."""

    level: str = "INFO"
    format: LogFormat = "json"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Top-level M0 application configuration."""

    project: ProjectConfig
    logging: LoggingConfig

    def as_dict(self) -> dict[str, Any]:
        """Return a serialization-safe dictionary."""

        return asdict(self)


def load_config(path: Path) -> AppConfig:
    """Load and strictly validate an M0 YAML configuration file.

    Args:
        path: UTF-8 YAML file path. Relative paths are resolved by the caller's working directory.

    Raises:
        ConfigError: If YAML syntax, keys, or values are invalid.
        OSError: If the file cannot be read.
    """

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in {path}: {error}") from error

    root = _mapping(raw, "configuration root")
    _reject_unknown(root, {"project", "logging"}, "configuration root")

    project_raw = _mapping(_required(root, "project", "configuration root"), "project")
    _reject_unknown(project_raw, {"name", "timezone"}, "project")
    project = ProjectConfig(
        name=_non_empty_string(_required(project_raw, "name", "project"), "project.name"),
        timezone=_non_empty_string(
            _required(project_raw, "timezone", "project"), "project.timezone"
        ),
    )

    logging_raw = _mapping(root.get("logging", {}), "logging")
    _reject_unknown(logging_raw, {"level", "format"}, "logging")
    level = _non_empty_string(logging_raw.get("level", "INFO"), "logging.level").upper()
    if level not in _LOG_LEVELS:
        allowed = ", ".join(sorted(_LOG_LEVELS))
        raise ConfigError(f"logging.level must be one of: {allowed}")

    log_format = _non_empty_string(logging_raw.get("format", "json"), "logging.format")
    if log_format not in {"json", "text"}:
        raise ConfigError("logging.format must be 'json' or 'text'")

    return AppConfig(
        project=project,
        logging=LoggingConfig(level=level, format=cast(LogFormat, log_format)),
    )


def _mapping(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{location} must be a mapping with string keys")
    return cast(dict[str, Any], value)


def _required(mapping: dict[str, Any], key: str, location: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"missing required key: {location}.{key}")
    return mapping[key]


def _non_empty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} must be a non-empty string")
    return value.strip()


def _reject_unknown(mapping: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(mapping.keys() - allowed)
    if unknown:
        raise ConfigError(f"unknown key(s) in {location}: {', '.join(unknown)}")
