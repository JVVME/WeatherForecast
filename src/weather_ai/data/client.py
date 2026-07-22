"""Injectable client boundary for CDS downloads."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Protocol, cast


class DownloadClient(Protocol):
    """Port used by the download service; tests provide an offline fake."""

    def download(self, dataset: str, request: dict[str, object], target: Path) -> None:
        """Retrieve one request into ``target``."""


class _CdsRetriever(Protocol):
    def retrieve(self, dataset: str, request: dict[str, object], target: str) -> object:
        """Submit a CDS retrieve request."""


class _CdsApiModule(Protocol):
    def Client(self) -> _CdsRetriever:  # noqa: N802 - matches the third-party API
        """Create the official CDS API client."""


class CdsApiDownloadClient:
    """Adapter around the official ``cdsapi.Client`` credential flow."""

    def download(self, dataset: str, request: dict[str, object], target: Path) -> None:
        """Download through cdsapi, which reads the user's local ``.cdsapirc``."""

        cdsapi = cast(_CdsApiModule, importlib.import_module("cdsapi"))
        client = cdsapi.Client()
        client.retrieve(dataset, request, str(target))
