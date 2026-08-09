"""Shared shape for open-data adapters.

Open-data endpoints are preferred over HTML portal scraping wherever a locality
publishes one: typed JSON with documented field names, no robots ambiguity, and
they do not break when the portal is restyled.

Every adapter normalizes its locality's field names into `PermitRecord`, so the
loader downstream never learns that Fairfax calls it `RECORDID` and Richmond
calls it `permit_number`.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx


def epoch_millis_to_date(value: Any) -> date | None:
    """ArcGIS returns dates as epoch milliseconds, sometimes negative or null."""
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).date()
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].rstrip("Z"), fmt).date()
        except ValueError:
            continue
    return None


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class PermitRecord:
    """One permit or land-use filing, normalized across localities."""

    locality: str
    permit_number: str

    permit_type: str | None = None
    description: str | None = None

    # The name to run through entity resolution. Which source field this comes
    # from varies: some localities publish an applicant, others only a project
    # name. `mention_field` records which, so a resolution can be audited.
    applicant_name_raw: str | None = None
    mention_field: str | None = None

    address: str | None = None
    parcel_id: str | None = None
    lat: float | None = None
    lon: float | None = None

    valuation_usd: float | None = None
    filed_date: date | None = None
    issued_date: date | None = None
    status: str | None = None

    source_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_mention(self) -> bool:
        """Whether there is any name to attempt firm attribution with."""
        return bool(self.applicant_name_raw and self.applicant_name_raw.strip())


# Fields that may come from a field_map — excludes those always passed explicitly.
_EXPLICIT_PERMIT_FIELDS: frozenset[str] = frozenset(
    {"locality", "permit_number", "mention_field", "raw"}
)
VALID_MAPPED_ATTRS: frozenset[str] = (
    frozenset(f.name for f in dataclasses.fields(PermitRecord)) - _EXPLICIT_PERMIT_FIELDS
)


class OpenDataAdapter(ABC):
    """Pulls permit records from one locality's open-data endpoint."""

    #: Value used in `sources.config["adapter"]` to select this class.
    name: str

    def __init__(self, locality: str, config: dict[str, Any]) -> None:
        self.locality = locality
        self.config = config

    @abstractmethod
    def validate(self) -> None:
        """Raise if the source config is missing what this adapter needs.

        Called before any network request so a misconfigured source fails with
        a message naming the missing key, rather than an opaque HTTP error.
        """

    @abstractmethod
    def fetch(
        self,
        *,
        since: date | None = None,
        limit: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> AsyncIterator[PermitRecord]:
        """Yield permit records, oldest-first for stable offset pagination."""
