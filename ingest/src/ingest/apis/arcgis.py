"""ArcGIS FeatureServer adapter.

Most Virginia localities publish land-development data through Esri, so one
adapter covers Fairfax, Loudoun, Prince William, Henrico, and Chesterfield —
only the service URL, layer id, and field map change.

A note on what this data can and cannot support: Fairfax's public permit layer
carries a project name but **no applicant, owner, or contractor field**. That
means most permits cannot be attributed to a developer by name from this feed
alone. `mention_field` records which column a name came from so a resolution is
auditable, and permits with no name at all are stored unattributed rather than
guessed at.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx

from ingest.apis.base import (
    VALID_MAPPED_ATTRS,
    OpenDataAdapter,
    PermitRecord,
    epoch_millis_to_date,
    to_float,
)

log = logging.getLogger(__name__)

# ArcGIS caps a single response; 1000 is the common server maximum and is
# respected regardless of what we ask for, so page rather than request more.
PAGE_SIZE = 500

# Stop paging a misconfigured or enormous layer rather than looping forever.
MAX_PAGES = 40


class ArcGISAdapter(OpenDataAdapter):
    """Reads a FeatureServer layer and normalizes it to PermitRecord.

    Required config:
        service_url  FeatureServer URL, without the layer index
        layer_id     integer layer index

    Optional config:
        field_map    source field -> PermitRecord attribute. Defaults suit the
                     Fairfax DevelopmentTracker schema.
        date_field   field to filter and sort on
        where        extra SQL predicate ANDed into every query
    """

    name = "arcgis"

    DEFAULT_FIELD_MAP: dict[str, str] = {
        "RECORDID": "permit_number",
        "APPTYPEALIAS": "permit_type",
        "PROJECT_NAME": "applicant_name_raw",
        "PARCEL_ID": "parcel_id",
        "RECORD_STATUS": "status",
        "ESTIMATED_COST": "valuation_usd",
        "SUBMITTED_DATE": "filed_date",
        "ISSUED_DATE": "issued_date",
        "MAR_ADDRESS": "address",
        "LINK_URL": "source_url",
    }

    DATE_ATTRS = {"filed_date", "issued_date"}
    FLOAT_ATTRS = {"valuation_usd"}

    def validate(self) -> None:
        if not self.config.get("service_url"):
            raise ValueError(
                f"{self.locality}: arcgis source needs config.service_url "
                "(the FeatureServer URL, without the layer index)"
            )
        if self.config.get("layer_id") is None:
            raise ValueError(f"{self.locality}: arcgis source needs config.layer_id")

    @property
    def query_url(self) -> str:
        base = str(self.config["service_url"]).rstrip("/")
        return f"{base}/{self.config['layer_id']}/query"

    @property
    def field_map(self) -> dict[str, str]:
        mapping = self.config.get("field_map")
        return dict(mapping) if mapping else dict(self.DEFAULT_FIELD_MAP)

    def _where(self, since: date | None) -> str:
        clauses = [str(self.config.get("where") or "1=1")]
        date_field = self.config.get("date_field")
        if since and date_field:
            # ArcGIS wants a DATE literal, not an epoch, in the where clause.
            clauses.append(f"{date_field} >= DATE '{since.isoformat()}'")
        return " AND ".join(clauses)

    def _to_record(
        self, attributes: dict[str, Any], geometry: dict[str, Any] | None
    ) -> PermitRecord | None:
        mapping = self.field_map
        values: dict[str, Any] = {}
        mention_field: str | None = None

        for source_field, attr in mapping.items():
            raw = attributes.get(source_field)
            if raw in (None, ""):
                continue
            if attr in self.DATE_ATTRS:
                values[attr] = epoch_millis_to_date(raw)
            elif attr in self.FLOAT_ATTRS:
                values[attr] = to_float(raw)
            else:
                values[attr] = str(raw).strip()
            if attr == "applicant_name_raw":
                mention_field = source_field

        permit_number = values.pop("permit_number", None)
        if not permit_number:
            # Without a permit number there is no natural key, so a re-run would
            # insert a second copy of the same filing on every crawl.
            return None

        if geometry:
            values["lat"] = to_float(geometry.get("y"))
            values["lon"] = to_float(geometry.get("x"))

        # Bug 46: filter to known PermitRecord fields so invalid field_map targets
        # don't crash with TypeError on unexpected keyword arguments.
        safe_values = {k: v for k, v in values.items() if k in VALID_MAPPED_ATTRS}
        return PermitRecord(
            locality=self.locality,
            permit_number=str(permit_number),
            mention_field=mention_field,
            raw=attributes,
            **safe_values,
        )

    async def fetch(
        self,
        *,
        since: date | None = None,
        limit: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> AsyncIterator[PermitRecord]:
        self.validate()

        owns_client = client is None
        client = client or httpx.AsyncClient(timeout=45.0, headers={"User-Agent": "PropIntel/0.1"})
        yielded = 0
        offset = 0

        try:
            for _ in range(MAX_PAGES):
                params: dict[str, str] = {
                    "where": self._where(since),
                    "outFields": "*",
                    "f": "json",
                    "returnGeometry": "true",
                    "outSR": "4326",  # WGS84, so lat/lon are usable directly
                    "resultOffset": str(offset),
                    "resultRecordCount": str(PAGE_SIZE),
                }
                date_field = self.config.get("date_field")
                if date_field:
                    # Bug 47: ASC keeps offset stable when new records arrive mid-crawl.
                    params["orderByFields"] = f"{date_field} ASC"

                response = await client.get(self.query_url, params=params)
                response.raise_for_status()
                payload = response.json()

                if "error" in payload:
                    # ArcGIS reports query errors inside a 200 response.
                    raise RuntimeError(f"{self.locality}: ArcGIS error {payload['error']}")

                features = payload.get("features") or []
                if not features:
                    return

                for feature in features:
                    record = self._to_record(
                        feature.get("attributes") or {}, feature.get("geometry")
                    )
                    if record is None:
                        continue
                    yield record
                    yielded += 1
                    if limit and yielded >= limit:
                        return

                if not payload.get("exceededTransferLimit") and len(features) < PAGE_SIZE:
                    return
                offset += len(features)
        finally:
            if owns_client:
                await client.aclose()
