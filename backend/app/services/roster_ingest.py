"""Persist team rosters and job postings, and signal what changed.

The value here is entirely in the *diff*. A team page is not interesting; a
name that appears where it wasn't last week is a key hire, and one that
disappears is a departure — both are competitive signals that lead permits by
months. Same for job postings: an open-role count that jumps is a firm staffing
up for something it has not filed yet.

Which means the correctness property that matters is: a crawl that fails to
render must never look like a firm that fired everyone. A roster that comes
back empty is treated as a failed crawl, not as a mass departure.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RoleType, SignalType
from app.models.person import JobPosting, Person
from app.models.signal import Signal
from app.services.naming import normalize_person

log = logging.getLogger(__name__)

# An executive hire is a stronger signal than a coordinator hire.
SENIOR_TITLE_MARKERS = (
    "chief",
    "ceo",
    "cfo",
    "coo",
    "president",
    "principal",
    "partner",
    "founder",
    "managing director",
    "head of",
    "svp",
    "evp",
    "vice president",
)

# Below this many postings, a week-over-week jump is noise rather than a surge.
HIRING_SURGE_MIN_OPEN = 5

# Proportional jump that counts as a surge.
HIRING_SURGE_RATIO = 1.5


@dataclass(slots=True)
class RosterStats:
    people_added: int = 0
    people_updated: int = 0
    departures: int = 0
    jobs_added: int = 0
    jobs_closed: int = 0
    jobs_reopened: int = 0
    signals_created: int = 0

    def __str__(self) -> str:
        return (
            f"people: {self.people_added} new, {self.people_updated} seen, "
            f"{self.departures} departed; jobs: {self.jobs_added} new, "
            f"{self.jobs_reopened} reopened, {self.jobs_closed} closed; "
            f"signals: {self.signals_created}"
        )


def classify_role(title: str | None) -> str:
    lowered = (title or "").lower()
    if any(marker in lowered for marker in SENIOR_TITLE_MARKERS):
        return RoleType.EXECUTIVE.value
    if "broker" in lowered or "leasing" in lowered:
        return RoleType.BROKER.value
    if "acquisition" in lowered:
        return RoleType.ACQUISITIONS.value
    if "construction" in lowered or "superintendent" in lowered:
        return RoleType.CONSTRUCTION.value
    if "develop" in lowered:
        return RoleType.DEVELOPER.value
    return RoleType.OTHER.value


def is_senior(title: str | None) -> bool:
    return any(marker in (title or "").lower() for marker in SENIOR_TITLE_MARKERS)


async def _add_signal(
    session: AsyncSession,
    *,
    firm_id: UUID,
    signal_type: str,
    title: str,
    summary: str,
    score: float,
    dedupe_key: str,
    source_document_id: UUID | None,
    payload: dict[str, Any],
    occurred_at: datetime | None = None,
) -> bool:
    """Insert a signal unless its dedupe key already exists."""
    exists = (
        await session.execute(select(Signal.id).where(Signal.dedupe_key == dedupe_key))
    ).scalar_one_or_none()
    if exists is not None:
        return False

    now = datetime.now(UTC)
    session.add(
        Signal(
            firm_id=firm_id,
            signal_type=signal_type,
            title=title[:400],
            summary=summary,
            score=score,
            occurred_at=occurred_at or now,
            detected_at=now,
            dedupe_key=dedupe_key[:200],
            source_document_id=source_document_id,
            payload=payload,
        )
    )
    # autoflush is off, so without this a second call in the same batch would
    # not see the pending row and would insert a duplicate.
    await session.flush()
    return True


async def ingest_roster(
    session: AsyncSession,
    *,
    firm_id: UUID,
    members: list[Any],
    source_document_id: UUID | None = None,
) -> RosterStats:
    """Reconcile a firm's people against a freshly-parsed team page."""
    stats = RosterStats()

    if not members:
        # A page that rendered nothing is indistinguishable from a page that
        # lists nobody. Marking everyone departed on an empty parse would
        # manufacture a mass-exodus signal out of a Playwright timeout.
        log.info("empty roster for firm %s; treating as a failed parse", firm_id)
        return stats

    now = datetime.now(UTC)
    existing = {
        person.canonical_name: person
        for person in (
            await session.execute(select(Person).where(Person.firm_id == firm_id))
        ).scalars()
    }

    seen: set[str] = set()

    for member in members:
        canonical = normalize_person(member.name)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)

        person = existing.get(canonical)
        if person is None:
            person = Person(
                firm_id=firm_id,
                name=member.name,
                canonical_name=canonical,
                title=member.job_title,
                role_type=classify_role(member.job_title),
                email=getattr(member, "email", None),
                linkedin_url=getattr(member, "linkedin_url", None),
                bio=getattr(member, "bio", None),
                is_current=True,
                first_seen_at=now,
                last_seen_at=now,
                source_document_id=source_document_id,
            )
            session.add(person)
            await session.flush()
            stats.people_added += 1

            if await _add_signal(
                session,
                firm_id=firm_id,
                signal_type=SignalType.KEY_HIRE.value,
                title=f"{member.name} joined as {member.job_title or 'team member'}",
                summary=(
                    f"{member.name} now appears on the team page as "
                    f"{member.job_title or 'a team member'}."
                ),
                # A senior hire is materially more interesting than a coordinator.
                score=0.8 if is_senior(member.job_title) else 0.45,
                dedupe_key=f"hire:{firm_id}:{canonical}:{now.year}:Q{(now.month - 1) // 3 + 1}",
                source_document_id=source_document_id,
                payload={"name": member.name, "title": member.job_title},
            ):
                stats.signals_created += 1
        else:
            person.last_seen_at = now
            person.is_current = True
            if member.job_title:
                person.title = member.job_title
                person.role_type = classify_role(member.job_title)
            stats.people_updated += 1

    # Anyone previously current who is absent from this render has departed.
    for canonical, person in existing.items():
        if canonical in seen or not person.is_current:
            continue
        person.is_current = False
        person.end_date = now.date()
        stats.departures += 1

        if await _add_signal(
            session,
            firm_id=firm_id,
            signal_type=SignalType.DEPARTURE.value,
            title=f"{person.name} no longer listed as {person.title or 'team member'}",
            summary=f"{person.name} has been removed from the firm's team page.",
            score=0.7 if is_senior(person.title) else 0.35,
            dedupe_key=f"departure:{firm_id}:{canonical}:{now.year}:Q{(now.month - 1) // 3 + 1}",
            source_document_id=source_document_id,
            payload={"name": person.name, "title": person.title},
        ):
            stats.signals_created += 1

    return stats


async def ingest_job_listings(
    session: AsyncSession,
    *,
    firm_id: UUID,
    listings: list[Any],
    source_document_id: UUID | None = None,
) -> RosterStats:
    """Reconcile a firm's open roles against a freshly-parsed careers page."""
    stats = RosterStats()

    if not listings:
        # Same reasoning as an empty roster: a failed render must not close
        # every open role and fabricate a hiring freeze.
        log.info("empty careers page for firm %s; treating as a failed parse", firm_id)
        return stats

    now = datetime.now(UTC)
    existing = {
        (posting.title.lower(), (posting.location or "").lower()): posting
        for posting in (
            await session.execute(select(JobPosting).where(JobPosting.firm_id == firm_id))
        ).scalars()
    }

    open_before = sum(1 for posting in existing.values() if posting.is_open)
    seen: set[tuple[str, str]] = set()

    for listing in listings:
        key = (listing.job_title.lower(), (listing.location or "").lower())
        if key in seen:
            continue
        seen.add(key)

        posting = existing.get(key)
        if posting is None:
            session.add(
                JobPosting(
                    firm_id=firm_id,
                    title=listing.job_title,
                    location=listing.location,
                    department=getattr(listing, "department", None),
                    employment_type=getattr(listing, "employment_type", None),
                    url=getattr(listing, "url", None),
                    is_open=True,
                    first_seen_at=now,
                    last_seen_at=now,
                    source_document_id=source_document_id,
                )
            )
            stats.jobs_added += 1
        else:
            if not posting.is_open:
                stats.jobs_reopened += 1
            posting.last_seen_at = now
            posting.is_open = True
            posting.closed_at = None

    for key, posting in existing.items():
        if key in seen or not posting.is_open:
            continue
        posting.is_open = False
        posting.closed_at = now
        stats.jobs_closed += 1

    await session.flush()
    open_after = open_before + stats.jobs_added + stats.jobs_reopened - stats.jobs_closed

    # A jump in open roles is a firm staffing up for work it has not filed yet.
    # Allow open_before == 0: going from zero to HIRING_SURGE_MIN_OPEN is a
    # valid surge (infinite ratio), so we only require the ratio when there is a
    # non-zero baseline to compare against.
    if open_after >= HIRING_SURGE_MIN_OPEN and (
        open_before == 0 or open_after >= open_before * HIRING_SURGE_RATIO
    ):
        period = now.date().isoformat()
        raw_key = f"{SignalType.HIRING_SURGE}:{firm_id}:{period}"
        dedupe_key = hashlib.sha256(raw_key.encode()).hexdigest()[:40]
        if await _add_signal(
            session,
            firm_id=firm_id,
            signal_type=SignalType.HIRING_SURGE.value,
            title=f"Open roles rose from {open_before} to {open_after}",
            summary=(
                f"The firm's careers page went from {open_before} to {open_after} "
                "open roles, which often precedes a new project."
            ),
            score=0.65,
            dedupe_key=dedupe_key,
            source_document_id=source_document_id,
            payload={"open_before": open_before, "open_after": open_after},
        ):
            stats.signals_created += 1

    return stats
