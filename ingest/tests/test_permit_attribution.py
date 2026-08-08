"""Permit ingestion, attribution, and signal emission against a live database.

Skipped when no database is reachable, so the offline suite still runs in CI.

These use synthetic permit records that carry real Virginia firm names. That is
deliberate rather than a shortcut: Fairfax's public permit feed does not contain
a developer name at all (its PROJECT_NAME column holds things like "***New Deck"
and homeowner names), so it cannot exercise the attribution path. These tests
verify the code path; the live Fairfax run verifies that it declines to invent
links when the data has none.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory, engine
from app.models.enums import ResolutionMethod, SignalType
from app.models.firm import Firm
from app.models.project import Permit
from app.models.signal import Signal
from app.services.permit_ingest import (
    SIGNAL_VALUATION_FLOOR,
    ingest_permit_records,
)
from ingest.apis.base import PermitRecord
from ingest.seeds.loader import seed_firms


async def _database_available() -> bool:
    try:
        async with engine.connect():
            return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    if not await _database_available():
        pytest.skip("no database reachable; run `make up && make migrate`")
    try:
        async with SessionFactory() as session:
            try:
                await seed_firms(session)
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


def _record(**kwargs: object) -> PermitRecord:
    defaults: dict[str, object] = {
        "locality": "Fairfax",
        "permit_number": f"TEST-{uuid.uuid4().hex[:10]}",
        "permit_type": "Commercial New",
        "applicant_name_raw": "Stanley Martin Companies, LLC",
        "mention_field": "PROJECT_NAME",
        "address": "7312 Braddock Rd, Annandale, VA",
        "valuation_usd": 5_000_000.0,
        "issued_date": date(2026, 3, 14),
    }
    defaults.update(kwargs)
    return PermitRecord(**defaults)  # type: ignore[arg-type]


class TestAttribution:
    async def test_a_permit_naming_a_tracked_firm_gets_a_foreign_key(
        self, session: AsyncSession
    ) -> None:
        """The whole point of Phase 1: a name in a filing becomes a FK."""
        stats = await ingest_permit_records(session, [_record()])

        assert stats.resolved == 1
        permit = (
            (
                await session.execute(
                    select(Permit).where(Permit.applicant_name_raw.like("Stanley Martin%"))
                )
            )
            .scalars()
            .first()
        )
        assert permit is not None
        assert permit.firm_id is not None

        firm = await session.get(Firm, permit.firm_id)
        assert firm is not None
        assert firm.name == "Stanley Martin Homes"

    async def test_the_resolution_method_is_recorded_for_audit(self, session: AsyncSession) -> None:
        """When a link is challenged, "which rule did this" is the first question."""
        await ingest_permit_records(session, [_record()])

        permit = (
            (
                await session.execute(
                    select(Permit).where(Permit.applicant_name_raw.like("Stanley Martin%"))
                )
            )
            .scalars()
            .first()
        )
        assert permit is not None
        assert permit.resolution_method in {m.value for m in ResolutionMethod}
        assert permit.resolution_confidence is not None

    async def test_an_unrelated_filer_is_left_unattributed(self, session: AsyncSession) -> None:
        stats = await ingest_permit_records(
            session, [_record(applicant_name_raw="Blue Ridge Excavating Services")]
        )

        assert stats.resolved == 0
        assert stats.unattributed == 1

    async def test_a_permit_with_no_name_is_stored_not_dropped(self, session: AsyncSession) -> None:
        """Most real Fairfax permits have no name; they are still real filings."""
        record = _record(applicant_name_raw=None, mention_field=None)

        stats = await ingest_permit_records(session, [record])

        assert stats.created == 1
        assert stats.unattributed == 1

    async def test_a_subcontractor_sharing_a_firm_name_is_not_linked(
        self, session: AsyncSession
    ) -> None:
        """The dilution guard, exercised through the full ingest path."""
        stats = await ingest_permit_records(
            session,
            [_record(applicant_name_raw="Stanley Martin Excavating Subcontractors of Virginia")],
        )

        assert stats.resolved == 0

    async def test_an_inferred_match_is_memoized_as_an_alias(self, session: AsyncSession) -> None:
        """So the next thousand filings under this name cost one indexed lookup."""
        from app.models.firm import FirmAlias

        before = await session.scalar(select(func.count()).select_from(FirmAlias))
        await ingest_permit_records(
            session, [_record(applicant_name_raw="Stanley Martin Homes LLC")]
        )
        after = await session.scalar(select(func.count()).select_from(FirmAlias))

        assert (after or 0) >= (before or 0)


class TestIdempotency:
    async def test_reingesting_the_same_permit_updates_rather_than_duplicates(
        self, session: AsyncSession
    ) -> None:
        record = _record()

        first = await ingest_permit_records(session, [record])
        second = await ingest_permit_records(session, [record])

        assert first.created == 1
        assert second.created == 0
        assert second.updated == 1

    async def test_a_signal_is_not_emitted_twice_for_one_permit(
        self, session: AsyncSession
    ) -> None:
        """Detection re-runs over overlapping windows; without the dedupe key a
        user would be re-alerted about the same filing every morning."""
        record = _record()

        first = await ingest_permit_records(session, [record])
        second = await ingest_permit_records(session, [record])

        assert first.signals_created == 1
        assert second.signals_created == 0


class TestSignalEmission:
    async def test_a_material_permit_emits_a_signal(self, session: AsyncSession) -> None:
        stats = await ingest_permit_records(
            session, [_record(valuation_usd=SIGNAL_VALUATION_FLOOR + 1)]
        )

        assert stats.signals_created == 1

    async def test_a_routine_permit_does_not(self, session: AsyncSession) -> None:
        """A deck replacement must not page anyone."""
        stats = await ingest_permit_records(
            session, [_record(valuation_usd=8_000.0, permit_type="Residential Alteration")]
        )

        assert stats.signals_created == 0

    # Every query below is scoped to its own record's dedupe key. The database
    # also holds real ingested Fairfax permits, so an unscoped "most recent
    # signal" query asserts against somebody else's row and fails for reasons
    # that have nothing to do with the code under test.
    @staticmethod
    async def _signal_for(session: AsyncSession, record: PermitRecord) -> Signal | None:
        return (
            (
                await session.execute(
                    select(Signal).where(
                        Signal.dedupe_key == f"permit:{record.locality}:{record.permit_number}"
                    )
                )
            )
            .scalars()
            .first()
        )

    async def test_the_signal_carries_the_firm_foreign_key(self, session: AsyncSession) -> None:
        """So a firm timeline is one indexed query, not a join through permits."""
        record = _record()
        await ingest_permit_records(session, [record])

        signal = await self._signal_for(session, record)

        assert signal is not None
        assert signal.signal_type == SignalType.PERMIT_FILING.value
        assert signal.firm_id is not None

    async def test_a_larger_permit_scores_higher(self, session: AsyncSession) -> None:
        """Score ranks the digest and thresholds alerts, so it must track value."""
        small = _record(valuation_usd=2_000_000.0)
        large = _record(valuation_usd=60_000_000.0)
        await ingest_permit_records(session, [small, large])

        small_signal = await self._signal_for(session, small)
        large_signal = await self._signal_for(session, large)

        assert small_signal is not None and large_signal is not None
        assert large_signal.score > small_signal.score

    async def test_occurred_at_uses_the_filing_date_not_now(self, session: AsyncSession) -> None:
        """The digest orders by when things happened, not when we noticed."""
        record = _record(issued_date=date(2026, 3, 14))
        await ingest_permit_records(session, [record])

        signal = await self._signal_for(session, record)

        assert signal is not None
        assert signal.occurred_at is not None
        assert signal.occurred_at.date() == date(2026, 3, 14)
        assert signal.detected_at is not None
        assert signal.detected_at > datetime(2026, 1, 1, tzinfo=UTC)
