"""Typed public records for M2-A plans, results, and manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PreprocessingStatus = Literal["success"]


@dataclass(frozen=True, slots=True)
class UnitConversionRecord:
    """One exact, non-inferred variable conversion."""

    input_name: str
    output_name: str
    input_units: str
    output_units: str
    operation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "input_name": self.input_name,
            "output_name": self.output_name,
            "input_units": self.input_units,
            "output_units": self.output_units,
            "operation": self.operation,
        }


@dataclass(frozen=True, slots=True)
class CoordinateNormalizationRecord:
    """Auditable coordinate renaming and ordering decisions."""

    source_names: dict[str, str]
    input_directions: dict[str, str]
    output_directions: dict[str, str]
    sorted_coordinates: tuple[str, ...]
    auxiliary_dimensions_removed: tuple[str, ...]
    auxiliary_coordinates_preserved: tuple[str, ...]
    longitude_domain_conversion: str = "none"
    time_semantics: str = "UTC"

    def as_dict(self) -> dict[str, object]:
        return {
            "source_names": self.source_names,
            "input_directions": self.input_directions,
            "output_directions": self.output_directions,
            "sorted_coordinates": list(self.sorted_coordinates),
            "auxiliary_dimensions_removed": list(self.auxiliary_dimensions_removed),
            "auxiliary_coordinates_preserved": list(self.auxiliary_coordinates_preserved),
            "longitude_domain_conversion": self.longitude_domain_conversion,
            "time_semantics": self.time_semantics,
        }


@dataclass(frozen=True, slots=True)
class PreprocessingVariableSummary:
    """Exact post-write statistics for one normalized variable."""

    name: str
    units: str
    dtype: str
    shape: tuple[int, ...]
    nan_count: int
    non_finite_count: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    standard_deviation: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "units": self.units,
            "dtype": self.dtype,
            "shape": list(self.shape),
            "nan_count": self.nan_count,
            "non_finite_count": self.non_finite_count,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "mean": self.mean,
            "standard_deviation": self.standard_deviation,
        }


@dataclass(frozen=True, slots=True)
class PreprocessingManifest:
    """Complete success evidence for one atomically published M2-A file."""

    schema_version: str
    preprocessing_version: str
    created_at_utc: str
    source_file: str
    source_file_size: int
    source_file_sha256: str
    source_download_manifest: str
    source_validation_report: str
    source_validation_status: str
    source_validation_warnings: tuple[dict[str, object], ...]
    source_dataset: str
    source_year: int
    source_month: int
    source_region: str
    input_variables: tuple[str, ...]
    output_variables: tuple[str, ...]
    unit_conversions: tuple[UnitConversionRecord, ...]
    coordinate_normalization: CoordinateNormalizationRecord
    dimension_order: tuple[str, str, str]
    output_file: str
    output_file_size: int
    output_file_sha256: str
    output_format: str
    software_version: str
    git_commit: str | None
    status: PreprocessingStatus
    variables: tuple[PreprocessingVariableSummary, ...]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible manifest without non-finite JSON numbers."""

        return {
            "schema_version": self.schema_version,
            "preprocessing_version": self.preprocessing_version,
            "created_at_utc": self.created_at_utc,
            "source_file": self.source_file,
            "source_file_size": self.source_file_size,
            "source_file_sha256": self.source_file_sha256,
            "source_download_manifest": self.source_download_manifest,
            "source_validation_report": self.source_validation_report,
            "source_validation_status": self.source_validation_status,
            "source_validation_warnings": list(self.source_validation_warnings),
            "source_dataset": self.source_dataset,
            "source_year": self.source_year,
            "source_month": self.source_month,
            "source_region": self.source_region,
            "input_variables": list(self.input_variables),
            "output_variables": list(self.output_variables),
            "unit_conversions": [conversion.as_dict() for conversion in self.unit_conversions],
            "coordinate_normalization": self.coordinate_normalization.as_dict(),
            "dimension_order": list(self.dimension_order),
            "output_file": self.output_file,
            "output_file_size": self.output_file_size,
            "output_file_sha256": self.output_file_sha256,
            "output_format": self.output_format,
            "software_version": self.software_version,
            "git_commit": self.git_commit,
            "status": self.status,
            "variables": [variable.as_dict() for variable in self.variables],
        }
