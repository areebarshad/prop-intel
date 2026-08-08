"""Persist development announcements as PropertyProject rows.

A permit is a fact; a news article is a report of one. Projects created here are
therefore marked `proposed`/`rumored` rather than `permitted`, and always carry
the article as their source document — so when a permit for the same project
later arrives from an open-data feed, the higher-confidence record can supersede
this one rather than duplicate it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ProjectStatus, ResolutionStatus, SignalType
from app.models.project import PropertyProject
from app.models.signal import Signal
from app.services.entity_resolution import EntityResolver, Mention
from app.services.naming import canonicalize

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ProjectStats:
    created: int = 0
    updated: int = 0
    resolved: int = 0
    unattributed: int = 0
    signals_created: int = 0

    def __str__(self) -> str:
        return (
            f"projects: {self.created} created, {self.updated} updated; "
            f"attribution: {self.resolved} resolved, {self.unattributed} unattributed; "
            f"signals: {self.signals_created}"
        )


def _project_score(announcement: Any) -> float:
    """Materiality, from whatever magnitude the article disclosed."""
    value = announcement.est_value_usd or 0
    units = announcement.unit_count or 0
    sqft = announcement.square_feet or 0

    if value >= 100_000_000 or units >= 500 or sqft >= 500_000:
        return 0.9
    if value >= 25_000_000 or units >= 200 or sqft >= 150_000:
        return 0.75
    if value >= 5_000_000 or units >= 50 or sqft >= 25_000:
        return 0.6
    return 0.45


def _signal_type_for(announcement: Any) -> str:
    headline = (announcement.headline or "").lower()
    if any(word in headline for word in ("acquire", "acquisition", "buys", "purchased")):
        return SignalType.LAND_ACQUISITION.value
    if "joint venture" in headline or " jv " in headline:
        return SignalType.JOINT_VENTURE.value
    return SignalType.NEW_PROJECT.value


async def ingest_announcement(
    session: AsyncSession,
    announcement: Any,
    *,
    source_document_id: UUID | None = None,
    resolver: EntityResolver | None = None,
) -> ProjectStats:
    """Persist one announcement, attributing it to a firm where possible."""
    stats = ProjectStats()

    name = announcement.project_name or announcement.headline
    if not name:
        return stats

    firm_id: UUID | None = None

    # Try the explicitly named firms first, then the headline itself — a
    # headline like "Comstock breaks ground in Reston" carries the firm name
    # even when no separate field does.
    if resolver is not None:
        for candidate in [*(announcement.firm_names or []), announcement.headline]:
            if not candidate:
                continue
            resolution = resolver.resolve(Mention(candidate))
            if resolution.is_resolved and resolution.firm_id:
                firm_id = UUID(resolution.firm_id)
                stats.resolved += 1
                break
            if resolution.status is ResolutionStatus.AMBIGUOUS:
                # News text is noisy; an ambiguous headline match is not worth
                # a review-queue entry the way a permit applicant is.
                log.debug("ambiguous firm mention in headline: %r", candidate)
        if firm_id is None:
            stats.unattributed += 1
    else:
        stats.unattributed += 1

    # Natural key: same project name in the same locality is the same project.
    canonical_name = canonicalize(name)
    existing = (
        await session.execute(
            select(PropertyProject).where(
                PropertyProject.name == name[:400],
                PropertyProject.locality == announcement.locality,
            )
        )
    ).scalar_one_or_none()

    fields = {
        "project_type": announcement.project_type,
        "locality": announcement.locality,
        "address": announcement.address,
        "unit_count": announcement.unit_count,
        "square_feet": announcement.square_feet,
        "acreage": announcement.acreage,
        "est_value_usd": announcement.est_value_usd,
        "announced_date": announcement.announced_date,
        "description": announcement.summary,
    }

    if existing is None:
        project = PropertyProject(
            name=name[:400],
            firm_id=firm_id,
            # News reports an intention, not an approval.
            status=ProjectStatus.PROPOSED.value,
            source_document_id=source_document_id,
            **fields,
        )
        session.add(project)
        await session.flush()
        stats.created += 1
    else:
        project = existing
        for key, value in fields.items():
            if value is not None:
                setattr(project, key, value)
        if firm_id is not None:
            project.firm_id = firm_id
        stats.updated += 1

    dedupe_key = f"announcement:{canonical_name}:{announcement.locality or 'va'}"[:200]
    exists = (
        await session.execute(select(Signal.id).where(Signal.dedupe_key == dedupe_key))
    ).scalar_one_or_none()

    if exists is None:
        now = datetime.now(UTC)
        session.add(
            Signal(
                firm_id=firm_id,
                signal_type=_signal_type_for(announcement),
                title=announcement.headline[:400],
                summary=announcement.summary,
                score=_project_score(announcement),
                locality=announcement.locality,
                asset_class=announcement.project_type,
                occurred_at=(
                    datetime.combine(announcement.announced_date, datetime.min.time(), UTC)
                    if announcement.announced_date
                    else now
                ),
                detected_at=now,
                dedupe_key=dedupe_key,
                source_document_id=source_document_id,
                payload={
                    "unit_count": announcement.unit_count,
                    "square_feet": announcement.square_feet,
                    "acreage": announcement.acreage,
                    "est_value_usd": announcement.est_value_usd,
                    # News is a report of a filing, not the filing — downstream
                    # consumers should weight it accordingly.
                    "confidence": "reported",
                },
            )
        )
        await session.flush()
        stats.signals_created += 1

    return stats
