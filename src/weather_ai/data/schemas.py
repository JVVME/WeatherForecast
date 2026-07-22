"""Typed, JSON-serializable models for ERA5 content validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

IssueSeverity = Literal["error", "warning", "info"]
ValidationStatus = Literal["passed", "passed_with_warnings", "failed"]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One stable validation finding."""

    severity: IssueSeverity
    code: str
    message: str
    variable: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return JSON-compatible built-in values."""

        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "variable": self.variable,
        }


@dataclass(frozen=True, slots=True)
class VariableSummary:
    """Structure, units, and exact finite-value statistics for one variable."""

    requested_name: str
    internal_name: str
    dimensions: tuple[str, ...]
    shape: tuple[int, ...]
    units: str | None
    total_count: int
    nan_count: int
    non_finite_count: int
    missing_ratio: float
    finite_ratio: float
    minimum: float | None
    maximum: float | None
    mean: float | None
    standard_deviation: float | None

    def as_dict(self) -> dict[str, object]:
        """Return JSON-compatible built-in values."""

        return {
            "requested_name": self.requested_name,
            "internal_name": self.internal_name,
            "dimensions": list(self.dimensions),
            "shape": list(self.shape),
            "units": self.units,
            "total_count": self.total_count,
            "nan_count": self.nan_count,
            "non_finite_count": self.non_finite_count,
            "missing_ratio": self.missing_ratio,
            "finite_ratio": self.finite_ratio,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "mean": self.mean,
            "standard_deviation": self.standard_deviation,
        }


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    """Observed NetCDF structure and coordinate semantics."""

    dimensions: dict[str, int]
    time_coordinate: str | None
    time_start: str | None
    time_end: str | None
    time_count: int | None
    time_resolution: str | None
    timezone: str
    latitude_coordinate: str | None
    latitude_min: float | None
    latitude_max: float | None
    latitude_direction: str | None
    longitude_coordinate: str | None
    longitude_min: float | None
    longitude_max: float | None
    longitude_direction: str | None
    coordinate_tolerance_degrees: float
    auxiliary_coordinates: tuple[str, ...]
    variable_mappings: dict[str, str | None]
    variables: tuple[VariableSummary, ...]

    def as_dict(self) -> dict[str, object]:
        """Return JSON-compatible built-in values."""

        return {
            "dimensions": self.dimensions,
            "time_coordinate": self.time_coordinate,
            "time_start": self.time_start,
            "time_end": self.time_end,
            "time_count": self.time_count,
            "time_resolution": self.time_resolution,
            "timezone": self.timezone,
            "latitude_coordinate": self.latitude_coordinate,
            "latitude_min": self.latitude_min,
            "latitude_max": self.latitude_max,
            "latitude_direction": self.latitude_direction,
            "longitude_coordinate": self.longitude_coordinate,
            "longitude_min": self.longitude_min,
            "longitude_max": self.longitude_max,
            "longitude_direction": self.longitude_direction,
            "coordinate_tolerance_degrees": self.coordinate_tolerance_degrees,
            "auxiliary_coordinates": list(self.auxiliary_coordinates),
            "variable_mappings": self.variable_mappings,
            "variables": [variable.as_dict() for variable in self.variables],
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Complete result for one immutable NetCDF and its download manifest."""

    file_path: str
    manifest_path: str
    file_size_bytes: int | None
    file_sha256: str | None
    file_sha256_after_validation: str | None
    status: ValidationStatus
    issues: tuple[ValidationIssue, ...]
    dataset: DatasetSummary | None

    @property
    def error_count(self) -> int:
        """Return the number of error findings."""

        return sum(issue.severity == "error" for issue in self.issues)

    @property
    def warning_count(self) -> int:
        """Return the number of warning findings."""

        return sum(issue.severity == "warning" for issue in self.issues)

    def as_dict(self) -> dict[str, object]:
        """Return the stable public JSON schema."""

        return {
            "file_path": self.file_path,
            "manifest_path": self.manifest_path,
            "file_size_bytes": self.file_size_bytes,
            "file_sha256": self.file_sha256,
            "file_sha256_after_validation": self.file_sha256_after_validation,
            "status": self.status,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [issue.as_dict() for issue in self.issues],
            "dataset": None if self.dataset is None else self.dataset.as_dict(),
        }


def validation_status(issues: list[ValidationIssue]) -> ValidationStatus:
    """Derive the report status solely from issue severities."""

    if any(issue.severity == "error" for issue in issues):
        return "failed"
    if any(issue.severity == "warning" for issue in issues):
        return "passed_with_warnings"
    return "passed"
