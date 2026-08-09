"""Socrata (SODA) adapter for localities on the Tyler/Socrata open-data stack.

Richmond, Arlington, and Alexandria publish through Socrata. Unlike ArcGIS,
Socrata datasets usually *do* carry an applicant or owner column, which makes
them the higher-value source for entity resolution where they exist.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx

from ingest.apis.base import OpenDataAdapter, PermitRecord, VALID_MAPPED_ATTRS, parse_iso_date, to_float

log = logging.getLogger(__name__)

PAGE_SIZE = 1000
MAX_PAGES = 40


class SocrataAdapter(OpenDataAdapter):
    """Reads a SODA dataset and normalizes it to PermitRecord.

    Required config:
        domain      e.g. data.richmondgov.com
        dataset_id  the 4x4 identifier, e.g. abcd-1234

    Optional config:
        field_map   source column -> PermitRecord attribute
        date_field  column to filter and sort on
        app_token   Socrata app token; without one requests are throttled
                    harder but still work
    """

    name = "socrata"

    # Used when no field_map is supplied in config.
    DEFAULT_FIELD_MAP: dict[str, str] = {
        "permit_number": "permit_number",
        "permit_type": "permit_type",
        "description": "description",
        "applicant_name": "applicant_name_raw",
        "address": "address",
        "parcel_id": "parcel_id",
        "valuation": "valuation_usd",
        "filed_date": "filed_date",
        "issued_date": "issued_date",
        "status": "status",
    }

    DATE_ATTRS = {"filed_date", "issued_date"}
    FLOAT_ATTRS = {"valuation_usd", "lat", "lon"}

    def validate(self) -> None:
        for key in ("domain", "dataset_id"):
            if not self.config.get(key):
                raise ValueError(f"{self.locality}: socrata source needs config.{key}")
        if "permit_number" not in self.field_map.values():
            raise ValueError(
                f"{self.locality}: field_map must map a source column to 'permit_number'"
            )

    @property
    def query_url(self) -> str:
        return f"https://{self.config['domain']}/resource/{self.config['dataset_id']}.json"

    @property
    def field_map(self) -> dict[str, str]:
        mapping = self.config.get("field_map")
        return dict(mapping) if mapping else dict(self.DEFAULT_FIELD_MAP)

    def _to_record(self, row: dict[str, Any]) -> PermitRecord | None:
        mapping = self.field_map
        values: dict[str, Any] = {}
        mention_field: str | None = None

        for source_field, attr in mapping.items():
            raw = row.get(source_field)
            if raw in (None, ""):
                continue
            if attr in self.DATE_ATTRS:
                values[attr] = parse_iso_date(raw)
            elif attr in self.FLOAT_ATTRS:
                values[attr] = to_float(raw)
            else:
                values[attr] = str(raw).strip()
            if attr == "applicant_name_raw":
                mention_field = source_field

        permit_number = values.pop("permit_number", None)
        if not permit_number:
            return None

        # Bug 46: filter to known PermitRecord fields so invalid field_map targets
        # don't crash with TypeError on unexpected keyword arguments.
        safe_values = {k: v for k, v in values.items() if k in VALID_MAPPED_ATTRS}
        return PermitRecord(
            locality=self.locality,
            permit_number=str(permit_number),
            mention_field=mention_field,
            raw=row,
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

        # Bug 48: build token header separately so it is applied regardless of
        # whether the client was created here or passed in by the caller.
        token_header: dict[str, str] = {}
        if self.config.get("app_token"):
            token_header["X-App-Token"] = str(self.config["app_token"])

        owns_client = client is None
        client = client or httpx.AsyncClient(timeout=45.0, headers={"User-Agent": "PropIntel/0.1"})
        yielded = 0
        offset = 0

        try:
            for _ in range(MAX_PAGES):
                params: dict[str, Any] = {
                    "$limit": PAGE_SIZE,
                    "$offset": offset,
                }
                date_field = self.config.get("date_field")
                if date_field:
                    # Bug 47: ASC sort keeps offset stable when new records arrive
                    # during a crawl; DESC would shift pages and skip rows.
                    params["$order"] = f"{date_field} ASC"
                    if since:
                        params["$where"] = f"{date_field} >= '{since.isoformat()}'"

                response = await client.get(self.query_url, params=params, headers=token_header)
                response.raise_for_status()
                rows = response.json()
                if not rows:
                    return

                for row in rows:
                    record = self._to_record(row)
                    if record is None:
                        continue
                    yield record
                    yielded += 1
                    if limit and yielded >= limit:
                        return

                if len(rows) < PAGE_SIZE:
                    return
                offset += len(rows)
        finally:
            if owns_client:
                await client.aclose()
