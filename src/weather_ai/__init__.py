"""WeatherAI project infrastructure."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("weather-ai")
except PackageNotFoundError:  # pragma: no cover - only possible without package installation
    __version__ = "0.0.0"

__all__ = ["__version__"]
