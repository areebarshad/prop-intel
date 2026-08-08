"""Open-data adapters, selected by the `adapter` key in a source's config."""

from __future__ import annotations

from typing import Any

from ingest.apis.arcgis import ArcGISAdapter
from ingest.apis.base import OpenDataAdapter, PermitRecord
from ingest.apis.socrata import SocrataAdapter

_ADAPTERS: dict[str, type[OpenDataAdapter]] = {
    ArcGISAdapter.name: ArcGISAdapter,
    SocrataAdapter.name: SocrataAdapter,
}


def available_adapters() -> list[str]:
    return sorted(_ADAPTERS)


def get_adapter(name: str, locality: str, config: dict[str, Any]) -> OpenDataAdapter:
    try:
        cls = _ADAPTERS[name]
    except KeyError:
        raise KeyError(
            f"unknown adapter {name!r}; available: {', '.join(available_adapters())}"
        ) from None
    return cls(locality, config)


__all__ = [
    "ArcGISAdapter",
    "OpenDataAdapter",
    "PermitRecord",
    "SocrataAdapter",
    "available_adapters",
    "get_adapter",
]
