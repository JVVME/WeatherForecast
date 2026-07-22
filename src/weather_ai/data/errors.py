"""Domain exceptions for ERA5 download operations."""


class DataDownloadError(RuntimeError):
    """Base error for a download that cannot be completed safely."""


class PreprocessingError(RuntimeError):
    """Raised when M2-A cannot safely produce a normalized intermediate file."""


class PreprocessingPreconditionError(PreprocessingError):
    """Raised when source provenance or the mandatory M1-B report is invalid."""


class PreprocessingValidationError(PreprocessingError):
    """Raised when raw or normalized data violates the M2-A contract."""


class CdsDownloadError(DataDownloadError):
    """Raised when the CDS client fails while preserving its exception as ``__cause__``."""


class TargetExistsError(DataDownloadError):
    """Raised when a final or temporary download path already exists."""


class DownloadValidationError(DataDownloadError):
    """Raised when a downloaded temporary file fails basic validation."""


class ManifestError(DataDownloadError):
    """Raised when a manifest cannot be read or updated safely."""
