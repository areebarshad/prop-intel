"""Persistence against a live PostgreSQL database.

Skipped automatically when no database is reachable, so the offline suite still
runs in CI on every push. Run these with a live database before trusting an
ingest change:

    make up && make migrate && uv run pytest ingest/tests/test_persistence.py
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory, engine
from app.models.document import RawDocument
from app.models.enums import SourceKind
from app.models.firm import Firm, FirmAlias
from app.models.source import Source
from ingest.runner import FetchOutcome, content_hash, looks_like_bot_challenge, persist_document
from ingest.seeds.loader import SeedError, seed_firms, validate_firms

_TEMPLATE = (
    "Henrico County Planning Commission approved case REZ2026-00014 for a "
    "240-unit multifamily development filed by Stanley Martin Companies. "
) * 4


async def _database_available() -> bool:
    try:
        async with engine.connect():
            return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A session whose writes are always rolled back.

    Tests must not leave rows behind — a later `status` call showing phantom
    documents would be indistinguishable from a real ingest.
    """
    if not await _database_available():
        pytest.skip("no database reachable; run `make up && make migrate`")

    try:
        async with SessionFactory() as session:
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        # pytest-asyncio gives each test its own event loop, but the engine is a
        # module-level singleton whose pool binds to the loop that first used it.
        # Reusing a pooled connection from a dead loop raises, which the
        # availability probe above then misreports as "no database reachable".
        # Disposing per test keeps each one on a connection from its own loop.
        await engine.dispose()


@pytest.fixture
def body() -> str:
    """Content unique to this test.

    These tests run against a shared development database that already holds
    real documents. Fixed content would collide with a previous run's hash and
    report DUPLICATE where the test expects STORED — a failure caused by the
    test, not the code.
    """
    return f"{_TEMPLATE} Reference {uuid.uuid4()}."


@pytest_asyncio.fixture
async def source(session: AsyncSession) -> Source:
    existing = (await session.execute(select(Source).limit(1))).scalars().first()
    if existing is not None:
        return existing

    source = Source(
        name="test source",
        kind=SourceKind.FIRM_SITE.value,
        base_url="https://example.test/fixture",
    )
    session.add(source)
    await session.flush()
    return source


class TestContentHashDedupe:
    """The property that makes scheduled re-crawls safe."""

    async def test_new_content_is_stored(
        self, session: AsyncSession, source: Source, body: str
    ) -> None:
        result = await persist_document(
            session, source=source, url="https://x.test/a", cleaned_text=body
        )

        assert result.outcome is FetchOutcome.STORED
        assert result.document_id

    async def test_the_same_content_at_a_new_url_is_a_duplicate(
        self, session: AsyncSession, source: Source, body: str
    ) -> None:
        """Portals routinely republish identical minutes under a new URL.

        Without this, every republish would re-extract, re-embed, and re-fire
        alerts for an event the user was already told about.
        """
        first = await persist_document(
            session, source=source, url="https://x.test/a", cleaned_text=body
        )
        second = await persist_document(
            session, source=source, url="https://x.test/different", cleaned_text=body
        )

        assert second.outcome is FetchOutcome.DUPLICATE
        assert second.document_id == first.document_id

    async def test_changed_content_at_the_same_url_is_stored(
        self, session: AsyncSession, source: Source, body: str
    ) -> None:
        await persist_document(session, source=source, url="https://x.test/a", cleaned_text=body)
        result = await persist_document(
            session,
            source=source,
            url="https://x.test/a",
            cleaned_text=body + " The case was deferred.",
        )

        assert result.outcome is FetchOutcome.STORED

    async def test_only_one_row_survives_a_repeated_crawl(
        self, session: AsyncSession, source: Source, body: str
    ) -> None:
        before = await session.scalar(select(func.count()).select_from(RawDocument))

        for _ in range(3):
            await persist_document(
                session, source=source, url="https://x.test/a", cleaned_text=body
            )

        after = await session.scalar(select(func.count()).select_from(RawDocument))
        assert after == (before or 0) + 1

    async def test_content_below_the_useful_threshold_is_not_stored(
        self, session: AsyncSession, source: Source
    ) -> None:
        """404 pages and empty JS shells cost an extraction and add nothing."""
        result = await persist_document(
            session, source=source, url="https://x.test/404", cleaned_text="Not found"
        )

        assert result.outcome is FetchOutcome.TOO_SHORT
        assert result.document_id is None

    def test_whitespace_does_not_change_the_hash(self, body: str) -> None:
        assert content_hash(body) == content_hash(f"\n  {body}  \n")

    def test_different_text_hashes_differently(self, body: str) -> None:
        assert content_hash(body) != content_hash(body + ".")


class TestBotChallengeDetection:
    """looks_like_bot_challenge and its effect on persist_document."""

    @pytest.mark.parametrize(
        "text",
        [
            "Performing security verification please wait",
            "Checking your browser before accessing the site",
            "Enable JavaScript and cookies to continue",
            "Verify you are human to proceed",
            "DDoS protection by Cloudflare",
            "Please enable cookies to continue",
            "Request unsuccessful. Incapsula incident id",
            "Access denied — you do not have permission to access this resource",
        ],
    )
    def test_detects_challenge_interstitials(self, text: str) -> None:
        assert looks_like_bot_challenge(text)

    def test_does_not_flag_real_content(self) -> None:
        real = (
            "Fairfax County Planning Commission approved a 240-unit multifamily "
            "development at 7312 Braddock Road. The applicant, Stanley Martin Companies, "
            "filed the site plan in January and expects construction to begin this summer. "
            "The project is valued at approximately $45 million and will include retail."
        )
        assert not looks_like_bot_challenge(real)

    async def test_bot_challenge_page_is_not_stored(
        self, session: AsyncSession, source: Source
    ) -> None:
        challenge = (
            "Performing security verification — please wait while we check your browser. "
            "This process is automatic. Your browser will redirect shortly. "
            "DDoS protection by Cloudflare. Ray ID: abc123. Performance & security."
        )
        result = await persist_document(
            session, source=source, url="https://x.test/blocked", cleaned_text=challenge
        )

        assert result.outcome is FetchOutcome.BOT_CHALLENGE
        assert result.document_id is None


class TestSeedLoader:
    async def test_seeding_twice_creates_no_duplicates(self, session: AsyncSession) -> None:
        await seed_firms(session)
        first = await session.scalar(select(func.count()).select_from(Firm))

        await seed_firms(session)
        second = await session.scalar(select(func.count()).select_from(Firm))

        assert first == second

    async def test_aliases_collapsing_to_one_canonical_form_yield_one_row(
        self, session: AsyncSession
    ) -> None:
        """ "Stanley Martin Companies, LLC" / "Companies" / "" all canonicalize
        to "stanley martin". That is the normalizer working, but the unique
        constraint permits exactly one row — and rows added earlier in the same
        transaction are invisible to a SELECT, so the loader must track them.
        """
        await seed_firms(session)

        duplicates = (
            await session.execute(
                select(FirmAlias.canonical_alias, func.count())
                .group_by(FirmAlias.canonical_alias)
                .having(func.count() > 1)
            )
        ).all()

        assert not duplicates

    async def test_every_firm_gets_a_website_source(self, session: AsyncSession) -> None:
        """Scoped to the seeded homepage source.

        `firm_site` also covers team pages found by `discover-team-pages`, so
        counting the whole kind compares seeded firms against seeded firms plus
        however many team pages happen to have been discovered.
        """
        await seed_firms(session)

        firms = await session.scalar(select(func.count()).select_from(Firm))
        site_sources = await session.scalar(
            select(func.count())
            .select_from(Source)
            .where(
                Source.kind == SourceKind.FIRM_SITE.value,
                Source.scrape_task == "firm_profile",
            )
        )

        assert site_sources == firms

    async def test_seeded_firms_carry_a_canonical_name(self, session: AsyncSession) -> None:
        """Entity resolution matches on this column, never the display name."""
        await seed_firms(session)

        missing = (
            await session.execute(
                select(Firm.name).where(
                    (Firm.canonical_name.is_(None)) | (Firm.canonical_name == "")
                )
            )
        ).all()

        assert not missing


class TestSeedValidation:
    """Validation runs before anything is written."""

    def test_an_unknown_asset_class_is_rejected(self) -> None:
        with pytest.raises(SeedError, match="asset_class"):
            validate_firms(
                [
                    {
                        "name": "Acme",
                        "website": "https://acme.test",
                        "asset_classes": ["spaceport"],
                    }
                ]
            )

    def test_an_unknown_firm_type_is_rejected(self) -> None:
        with pytest.raises(SeedError, match="firm_type"):
            validate_firms(
                [
                    {
                        "name": "Acme",
                        "website": "https://acme.test",
                        "firm_type": "wizard",
                    }
                ]
            )

    def test_a_missing_website_is_rejected(self) -> None:
        with pytest.raises(SeedError, match="website"):
            validate_firms([{"name": "Acme"}])

    def test_two_firms_sharing_a_slug_are_rejected(self) -> None:
        """They would silently overwrite each other on the next seed run."""
        with pytest.raises(SeedError, match="slugify"):
            validate_firms(
                [
                    {"name": "Acme Corp", "website": "https://a.test"},
                    {"name": "Acme  Corp!", "website": "https://b.test"},
                ]
            )
