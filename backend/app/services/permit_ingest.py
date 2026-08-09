"""Turn normalized permit records into Permit rows, resolved and signalled.

This is where Phase 1's two halves meet: an open-data record arrives with a
name as a string, and leaves as a row whose `firm_id` points at a tracked
developer — or explicitly does not, with the reason recorded.

Three outcomes per record, all of them acceptable except a silent wrong link:

  * resolved      firm_id set, method and confidence recorded
  * ambiguous     queued in entity_candidates for review, firm_id left null
  * unattributed  no name in the source data at all (common: Fairfax's public
                  permit feed carries a project name but no applicant field)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.document import EntityCandidate
from app.models.enums import (
    AliasType,
    ResolutionMethod,
    ResolutionStatus,
    SignalType,
)
from app.models.firm import Firm, FirmAlias
from app.models.project import Permit
from app.models.signal import Signal
from app.services.entity_resolution import EntityResolver, FirmRecord, Mention
from app.services.naming import canonicalize

log = logging.getLogger(__name__)

# Permits below this valuation are routine — water heaters, decks, signage.
# They would drown the signal feed, so they are stored but not signalled.
SIGNAL_VALUATION_FLOOR = 1_000_000.0


@dataclass(slots=True)
class PermitIngestStats:
    created: int = 0
    updated: int = 0
    resolved: int = 0
    ambiguous: int = 0
    unattributed: int = 0
    signals_created: int = 0

    def __str__(self) -> str:
        return (
            f"permits: {self.created} created, {self.updated} updated; "
            f"attribution: {self.resolved} resolved, {self.ambiguous} queued, "
            f"{self.unattributed} unattributed; signals: {self.signals_created}"
        )


async def load_firm_records(session: AsyncSession) -> list[FirmRecord]:
    """Build the resolver's view of the firm table, aliases included."""
    firms = list(
        (await session.execute(select(Firm).options(selectinload(Firm.aliases)))).scalars()
    )
    # Collect known parcel_ids per firm from attributed permits so corroboration
    # can fire on parcel evidence without a separate join per mention.
    parcel_rows = list(
        (
            await session.execute(
                select(Permit.firm_id, Permit.parcel_id).where(
                    Permit.firm_id.is_not(None), Permit.parcel_id.is_not(None)
                ).distinct()
            )
        ).all()
    )
    firm_parcel_map: dict[str, list[str]] = {}
    for fid, pid in parcel_rows:
        firm_parcel_map.setdefault(str(fid), []).append(pid)

    return [
        FirmRecord(
            id=str(firm.id),
            name=firm.name,
            canonical_name=firm.canonical_name,
            aliases=tuple(alias.alias for alias in firm.aliases),
            address=firm.hq_address,
            phone=firm.phone,
            parcel_ids=tuple(firm_parcel_map.get(str(firm.id), [])),
        )
        for firm in firms
    ]


def _permit_score(valuation: float | None, permit_type: str | None) -> float:
    """How much a permit deserves a user's attention, 0-1.

    Valuation is the strongest available proxy for materiality; permit type
    breaks ties when a locality omits cost.
    """
    score = 0.35
    if valuation:
        if valuation >= 50_000_000:
            score = 0.95
        elif valuation >= 10_000_000:
            score = 0.85
        elif valuation >= 5_000_000:
            score = 0.7
        elif valuation >= 1_000_000:
            score = 0.55
    kind = (permit_type or "").lower()
    if any(word in kind for word in ("commercial", "site plan", "rezoning", "data center")):
        score = min(1.0, score + 0.1)
    return round(score, 2)


async def _record_alias(
    session: AsyncSession,
    firm_id: str,
    mention: str,
    method: ResolutionMethod,
    confidence: float,
    *,
    resolver: EntityResolver | None = None,
) -> None:
    """Memoize an inferred match so the next filing under this name is exact.

    This is what makes the corpus cheaper over time: the first encounter with a
    new project LLC may cost a fuzzy pass or an LLM call, the next thousand cost
    one indexed lookup.
    """
    canonical = canonicalize(mention)
    if not canonical:
        return
    exists = (
        await session.execute(select(FirmAlias.id).where(FirmAlias.canonical_alias == canonical))
    ).scalar_one_or_none()
    if exists is not None:
        return
    session.add(
        FirmAlias(
            firm_id=UUID(firm_id),
            alias=mention,
            canonical_alias=canonical,
            alias_type=AliasType.LLC_HOLDING.value,
            confidence=confidence,
            resolution_method=method.value,
        )
    )
    # Make it visible to the next lookup in this same batch.
    await session.flush()
    # Sync the in-memory alias map so subsequent mentions in the same batch
    # resolve via the cheap exact path rather than re-running fuzzy/LLM.
    if resolver is not None:
        firm_record = next((f for f in resolver.firms if f.id == firm_id), None)
        if firm_record is not None:
            resolver._by_alias.setdefault(canonical, firm_record)


async def _queue_for_review(
    session: AsyncSession, mention: str, candidates: list[Any], permit_id: UUID | None
) -> None:
    """Park an ambiguous mention instead of guessing.

    A wrong link silently credits a competitor's activity to the wrong firm and
    is invisible; a gap in the queue is visible and fixable.
    """
    canonical = canonicalize(mention)
    if not canonical:
        return
    exists = (
        await session.execute(
            select(EntityCandidate.id).where(EntityCandidate.canonical_mention == canonical)
        )
    ).scalar_one_or_none()
    if exists is not None:
        return
    session.add(
        EntityCandidate(
            mention=mention,
            canonical_mention=canonical,
            origin_table="permits",
            origin_id=permit_id,
            status=ResolutionStatus.AMBIGUOUS.value,
            best_score=(candidates[0].effective_score / 100.0) if candidates else None,
            suggestions=[
                {
                    "firm_id": c.firm.id,
                    "name": c.firm.name,
                    "score": round(c.effective_score, 1),
                }
                for c in candidates[:5]
            ],
        )
    )
    await session.flush()


async def ingest_permit_records(
    session: AsyncSession,
    records: list[Any],
    *,
    source_document_id: UUID | None = None,
    resolver: EntityResolver | None = None,
) -> PermitIngestStats:
    """Persist permit records, attribute them to firms, and emit signals."""
    stats = PermitIngestStats()
    if not records:
        return stats

    resolver = resolver or EntityResolver(await load_firm_records(session))
    now = datetime.now(UTC)

    for record in records:
        existing = (
            await session.execute(
                select(Permit).where(
                    Permit.locality == record.locality,
                    Permit.permit_number == record.permit_number,
                )
            )
        ).scalar_one_or_none()

        firm_id: UUID | None = None
        method: str | None = None
        confidence: float | None = None

        ambiguous_mention: str | None = None
        ambiguous_candidates: list[Any] = []

        if record.has_mention:
            resolution = resolver.resolve(
                Mention(
                    record.applicant_name_raw,
                    address=record.address,
                    parcel_id=getattr(record, "parcel_id", None),
                )
            )
            if resolution.is_resolved and resolution.firm_id:
                firm_id = UUID(resolution.firm_id)
                method = resolution.method.value if resolution.method else None
                confidence = resolution.confidence
                stats.resolved += 1
                if resolution.should_write_alias and resolution.method:
                    await _record_alias(
                        session,
                        resolution.firm_id,
                        record.applicant_name_raw,
                        resolution.method,
                        resolution.confidence,
                        resolver=resolver,
                    )
            elif resolution.status is ResolutionStatus.AMBIGUOUS:
                stats.ambiguous += 1
                # Defer _queue_for_review until after the permit is created so
                # we can pass the correct permit.id as origin_id.
                ambiguous_mention = record.applicant_name_raw
                ambiguous_candidates = resolution.candidates
            else:
                stats.unattributed += 1
        else:
            stats.unattributed += 1

        fields = {
            "permit_type": record.permit_type,
            "description": record.description,
            "applicant_name_raw": record.applicant_name_raw,
            "address": record.address,
            "parcel_id": record.parcel_id,
            "lat": record.lat,
            "lon": record.lon,
            "valuation_usd": record.valuation_usd,
            "filed_date": record.filed_date,
            "issued_date": record.issued_date,
            "status": record.status,
            "resolution_method": method,
            "resolution_confidence": confidence,
        }

        if existing is None:
            permit = Permit(
                locality=record.locality,
                permit_number=record.permit_number,
                firm_id=firm_id,
                source_document_id=source_document_id,
                **fields,
            )
            session.add(permit)
            await session.flush()
            stats.created += 1
        else:
            permit = existing
            for key, value in fields.items():
                # Always update attribution metadata even when the new value is
                # None — stale method/confidence on a permit whose re-resolution
                # failed is misleading and must be cleared.
                if key in ("resolution_method", "resolution_confidence"):
                    setattr(permit, key, value)
                elif value is not None:
                    setattr(permit, key, value)
            # Never clear an attribution that a later run failed to reproduce —
            # a human may have set it, and losing it silently is worse than a
            # stale one.
            if firm_id is not None:
                permit.firm_id = firm_id
            stats.updated += 1

        # Queue ambiguous mentions now that we have a permit.id to link back to.
        if ambiguous_mention:
            await _queue_for_review(
                session,
                ambiguous_mention,
                ambiguous_candidates,
                permit.id,
            )

        if await _emit_permit_signal(session, permit, record, now):
            stats.signals_created += 1

    return stats


async def _emit_permit_signal(
    session: AsyncSession, permit: Permit, record: Any, now: datetime
) -> bool:
    """Add a signal for a material permit. Returns whether one was created."""
    valuation = float(record.valuation_usd or 0)
    if valuation < SIGNAL_VALUATION_FLOOR:
        return False

    # Natural key, so re-running extraction over the same feed cannot produce a
    # second copy of the same event.
    dedupe_key = f"permit:{record.locality}:{record.permit_number}"
    exists = (
        await session.execute(select(Signal.id).where(Signal.dedupe_key == dedupe_key))
    ).scalar_one_or_none()
    if exists is not None:
        return False

    name = record.applicant_name_raw or record.permit_number
    where = record.address or record.locality
    amount = f"${valuation:,.0f}" if valuation else "undisclosed value"

    session.add(
        Signal(
            firm_id=permit.firm_id,
            signal_type=SignalType.PERMIT_FILING.value,
            title=f"{name} — {record.permit_type or 'permit'} in {record.locality}",
            summary=(
                f"{name} filed a {record.permit_type or 'permit'} at {where} valued at {amount}."
            ),
            score=_permit_score(record.valuation_usd, record.permit_type),
            locality=record.locality,
            occurred_at=(
                datetime.combine(record.issued_date or record.filed_date, datetime.min.time(), UTC)
                if (record.issued_date or record.filed_date)
                else None
            ),
            detected_at=now,
            dedupe_key=dedupe_key,
            source_document_id=permit.source_document_id,
            payload={
                "permit_number": record.permit_number,
                "parcel_id": record.parcel_id,
                "valuation_usd": valuation,
                "mention_field": record.mention_field,
            },
        )
    )
    await session.flush()
    return True
