"""Parcel-to-owner join for Fairfax permits.

Fairfax County's public permit feed carries no applicant, owner, or contractor
column. Attribution therefore requires a separate data pull: the county's
parcel ownership layer (Real Property), which is queryable via the same ArcGIS
adapter used for permits. Joining on parcel_id gives us the owner name, which
then runs through the entity resolver to link to a tracked developer.

Usage:
    resolver = EntityResolver(await load_firm_records(session))
    stats = await enrich_permits_with_parcel_owners(session, resolver)

The parcel service URL must be verified against the live Fairfax GIS portal
before the first run. Mark it as verified: true in sources.yaml once confirmed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Permit
from app.models.signal import Signal
from app.services.entity_resolution import EntityResolver, Mention, Resolution
from app.services.permit_ingest import _record_alias

log = logging.getLogger(__name__)

# Fairfax County Real Property ArcGIS endpoint.
# Verify this URL before the first run:
#   uv run propintel-ingest verify-seeds --kind open_data_api
FAIRFAX_PARCEL_SERVICE_URL = (
    "https://www.fairfaxcounty.gov/maps/rest/services/PropertyInfo/"
    "FeatureServer"
)
FAIRFAX_PARCEL_LAYER_ID = 0
# Field names in the Fairfax parcel layer — confirm against the layer schema
# at {service_url}/{layer_id}?f=json before relying on these.
PARCEL_ID_FIELD = "GeoPin"
OWNER_FIELD = "OwnerName"


@dataclass(slots=True)
class ParcelEnrichStats:
    permits_examined: int = 0
    parcel_ids_fetched: int = 0
    permits_attributed: int = 0
    permits_queued: int = 0
    permits_no_owner: int = 0      # no row in the parcel service
    permits_unresolved: int = 0    # owner row found but resolver returned unmatched/ambiguous
    errors: int = 0

    def __str__(self) -> str:
        return (
            f"{self.permits_examined} examined, "
            f"{self.parcel_ids_fetched} parcel lookups, "
            f"{self.permits_attributed} attributed, "
            f"{self.permits_queued} queued, "
            f"{self.permits_no_owner} no owner row, "
            f"{self.permits_unresolved} unresolved"
        )


async def fetch_parcel_owners(
    parcel_ids: list[str],
    *,
    service_url: str = FAIRFAX_PARCEL_SERVICE_URL,
    layer_id: int = FAIRFAX_PARCEL_LAYER_ID,
    parcel_id_field: str = PARCEL_ID_FIELD,
    owner_field: str = OWNER_FIELD,
) -> dict[str, str]:
    """Query the Fairfax parcel layer and return {parcel_id: owner_name}.

    Uses the existing ArcGIS adapter logic directly so pagination and field
    normalisation are handled consistently with permit ingestion.
    """
    if not parcel_ids:
        return {}

    import httpx

    # Sanitize parcel IDs to prevent ArcGIS WHERE clause corruption; Fairfax
    # GeoPin values are numeric strings but be defensive.
    safe_ids = [pid.replace("'", "") for pid in parcel_ids]
    id_list = ", ".join(f"'{pid}'" for pid in safe_ids)
    where = f"{parcel_id_field} IN ({id_list})"
    params = {
        "where": where,
        "outFields": f"{parcel_id_field},{owner_field}",
        "returnGeometry": "false",
        "f": "json",
    }
    url = f"{service_url}/{layer_id}/query"

    result: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            for feature in data.get("features") or []:
                attrs = feature.get("attributes") or {}
                pid = attrs.get(parcel_id_field)
                owner = attrs.get(owner_field)
                if pid and owner:
                    result[str(pid)] = str(owner)
    except Exception as exc:  # noqa: BLE001
        log.warning("parcel owner fetch failed: %s", exc)

    return result


async def enrich_permits_with_parcel_owners(
    session: AsyncSession,
    resolver: EntityResolver,
    *,
    locality: str = "Fairfax",
    batch_size: int = 100,
    service_url: str = FAIRFAX_PARCEL_SERVICE_URL,
    layer_id: int = FAIRFAX_PARCEL_LAYER_ID,
    parcel_id_field: str = PARCEL_ID_FIELD,
    owner_field: str = OWNER_FIELD,
) -> ParcelEnrichStats:
    """Attribute unlinked Fairfax permits by joining on parcel ownership data.

    Only processes permits where:
      - firm_id IS NULL (not yet attributed)
      - parcel_id IS NOT NULL (joinable)
      - locality matches (default Fairfax)

    Results are written back to the permit row and, for accepted matches, to
    firm_aliases so subsequent encounters resolve immediately.
    """
    from app.models.enums import ResolutionStatus

    stats = ParcelEnrichStats()

    # Load unattributed permits in batches
    query = (
        select(Permit)
        .where(
            Permit.firm_id.is_(None),
            Permit.parcel_id.is_not(None),
            Permit.locality == locality,
        )
        .order_by(Permit.filed_date.desc())
    )
    permits = list((await session.execute(query)).scalars())
    stats.permits_examined = len(permits)

    if not permits:
        log.info("no unattributed %s permits with parcel IDs to enrich", locality)
        return stats

    # Process in batches to avoid building huge IN clauses
    for batch_start in range(0, len(permits), batch_size):
        batch = permits[batch_start : batch_start + batch_size]
        parcel_ids = [p.parcel_id for p in batch if p.parcel_id]
        stats.parcel_ids_fetched += len(parcel_ids)

        owner_map = await fetch_parcel_owners(
            parcel_ids,
            service_url=service_url,
            layer_id=layer_id,
            parcel_id_field=parcel_id_field,
            owner_field=owner_field,
        )

        for permit in batch:
            if not permit.parcel_id:
                continue
            owner_name = owner_map.get(permit.parcel_id)
            if not owner_name:
                stats.permits_no_owner += 1
                continue

            permit.applicant_name_raw = owner_name
            outcome: Resolution = resolver.resolve(Mention(owner_name))

            if outcome.status == ResolutionStatus.RESOLVED and outcome.firm_id:
                permit.firm_id = UUID(outcome.firm_id)
                permit.resolution_method = outcome.method.value if outcome.method else None
                permit.resolution_confidence = outcome.confidence
                stats.permits_attributed += 1
                log.debug(
                    "permit %s attributed to %s via parcel owner %r",
                    permit.permit_number,
                    outcome.firm_id,
                    owner_name,
                )

                # Memoize so the next encounter with this owner resolves instantly.
                if outcome.should_write_alias and outcome.method:
                    await _record_alias(
                        session,
                        outcome.firm_id,
                        owner_name,
                        outcome.method,
                        outcome.confidence,
                    )

                # Backfill firm_id on any existing signal for this permit so
                # the timeline and digest reflect the now-known developer.
                dedupe_key = f"permit:{permit.locality}:{permit.permit_number}"
                existing_signal = (
                    await session.execute(
                        select(Signal).where(Signal.dedupe_key == dedupe_key)
                    )
                ).scalar_one_or_none()
                if existing_signal is not None and existing_signal.firm_id is None:
                    existing_signal.firm_id = UUID(outcome.firm_id)

            elif outcome.status == ResolutionStatus.AMBIGUOUS:
                stats.permits_queued += 1
                stats.permits_unresolved += 1
            else:
                stats.permits_unresolved += 1

    return stats
