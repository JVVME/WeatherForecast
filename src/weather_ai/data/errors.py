"""Domain exceptions for ERA5 download operations."""


class DataDownloadError(RuntimeError):
    """Base error for a download that cannot be completed safely."""


class TargetExistsError(DataDownloadError):
    """Raised when a final or temporary download path already exists."""


class DownloadValidationError(DataDownloadError):
    """Raised when a downloaded temporary file fails basic validation."""


class ManifestError(DataDownloadError):
    """Raised when a manifest cannot be read or updated safely."""
