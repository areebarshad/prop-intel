"""Load the seed YAML into the database.

Idempotent by design: `make seed` runs on every fresh environment and after
every seed-file edit, so a second run must update rather than duplicate. Firms
are keyed by slug, aliases by canonical form, sources by base URL — the same
natural keys the database enforces.

**Additive only.** Removing a firm or source from the YAML does not delete its
rows. That is deliberate: `firms` cascades to permits, people, job postings, and
signals, so a delete would discard collected intelligence to reflect an edit to
a seed file. Retiring a firm is an explicit operation:

    UPDATE sources SET is_active = false WHERE firm_id = '...';

The consequence to know about: after removing a row from the YAML, the old
source is still crawled until it is deactivated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AliasType, AssetClass, FirmType, ResolutionMethod, SourceKind
from app.models.firm import Firm, FirmAlias
from app.models.source import Source
from app.services.naming import canonicalize, slugify

log = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent
FIRMS_FILE = SEED_DIR / "firms.yaml"
SOURCES_FILE = SEED_DIR / "sources.yaml"


class SeedError(ValueError):
    """A seed file is malformed. Raised before anything is written."""


@dataclass(slots=True)
class SeedStats:
    firms_created: int = 0
    firms_updated: int = 0
    aliases_created: int = 0
    sources_created: int = 0
    sources_updated: int = 0

    def __str__(self) -> str:
        return (
            f"firms: {self.firms_created} created, {self.firms_updated} updated; "
            f"aliases: {self.aliases_created} created; "
            f"sources: {self.sources_created} created, {self.sources_updated} updated"
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SeedError(f"seed file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SeedError(f"{path.name}: expected a mapping at the top level")
    return data


def validate_firms(rows: list[dict[str, Any]]) -> None:
    """Fail loudly before writing anything.

    A typo'd asset class would otherwise sit in the database until a search
    filter mysteriously returned nothing — validating here turns that into an
    error at seed time with the offending firm named.
    """
    valid_types = {t.value for t in FirmType}
    valid_assets = {a.value for a in AssetClass}
    seen_slugs: dict[str, str] = {}

    for row in rows:
        name = row.get("name")
        if not name:
            raise SeedError(f"firm row missing 'name': {row!r}")
        if not row.get("website"):
            raise SeedError(f"{name}: missing 'website'")

        firm_type = row.get("firm_type", FirmType.UNKNOWN.value)
        if firm_type not in valid_types:
            raise SeedError(f"{name}: firm_type {firm_type!r} is not one of {sorted(valid_types)}")

        for asset in row.get("asset_classes") or []:
            if asset not in valid_assets:
                raise SeedError(
                    f"{name}: asset_class {asset!r} is not one of {sorted(valid_assets)}"
                )

        slug = slugify(name)
        if slug in seen_slugs and seen_slugs[slug] != name:
            raise SeedError(f"{name!r} and {seen_slugs[slug]!r} both slugify to {slug!r}")
        seen_slugs[slug] = name


def validate_sources(rows: list[dict[str, Any]]) -> None:
    valid_kinds = {k.value for k in SourceKind}
    seen_urls: dict[str, str] = {}

    for row in rows:
        name = row.get("name")
        if not name:
            raise SeedError(f"source row missing 'name': {row!r}")

        kind = row.get("kind")
        if kind not in valid_kinds:
            raise SeedError(f"{name}: kind {kind!r} is not one of {sorted(valid_kinds)}")

        base_url = row.get("base_url")
        if not base_url:
            raise SeedError(f"{name}: missing 'base_url'")
        if base_url in seen_urls:
            raise SeedError(f"{name!r} and {seen_urls[base_url]!r} share base_url {base_url}")
        seen_urls[base_url] = name


async def _upsert_firm(session: AsyncSession, row: dict[str, Any], stats: SeedStats) -> Firm:
    slug = slugify(row["name"])
    existing = (await session.execute(select(Firm).where(Firm.slug == slug))).scalar_one_or_none()

    fields = {
        "name": row["name"],
        "canonical_name": canonicalize(row["name"]),
        "website": row.get("website"),
        "firm_type": row.get("firm_type", FirmType.UNKNOWN.value),
        "city": row.get("city"),
        "county": row.get("county"),
        "state": row.get("state", "VA"),
        "hq_address": row.get("hq_address"),
        "phone": row.get("phone"),
        "focus_areas": row.get("focus_areas") or [],
        "asset_classes": row.get("asset_classes") or [],
        "localities": row.get("localities") or [],
        "founded_year": row.get("founded_year"),
        "description": row.get("description"),
    }

    if existing is None:
        firm = Firm(slug=slug, first_seen_at=datetime.now(UTC), **fields)
        session.add(firm)
        await session.flush()
        stats.firms_created += 1
        return firm

    # Seed files are the source of truth for these fields; anything the
    # pipeline discovers later (embeddings, activity dates, pipeline counts) is
    # deliberately left untouched so re-seeding never erases learned data.
    for key, value in fields.items():
        setattr(existing, key, value)
    stats.firms_updated += 1
    return existing


async def _upsert_aliases(
    session: AsyncSession,
    firm: Firm,
    row: dict[str, Any],
    stats: SeedStats,
    seen: set[str],
) -> None:
    aliases = list(row.get("aliases") or [])

    # The firm's own name is an alias too, so a filing that renders it slightly
    # differently still short-circuits at the alias lookup.
    aliases.append(row["name"])

    for alias in aliases:
        canonical = canonicalize(alias)
        if not canonical or canonical == firm.canonical_name:
            continue

        # Several spellings routinely collapse to one canonical form —
        # "Stanley Martin Companies, LLC", "Stanley Martin Companies", and
        # "Stanley Martin" are all "stanley martin". That is the normalizer
        # working, but only one row may exist. `seen` is required because rows
        # added earlier in this transaction are not yet visible to a SELECT.
        if canonical in seen:
            continue
        seen.add(canonical)

        taken = (
            await session.execute(select(FirmAlias).where(FirmAlias.canonical_alias == canonical))
        ).scalar_one_or_none()

        if taken is not None:
            if taken.firm_id != firm.id:
                # Two seeded firms claiming one alias is a data error that would
                # otherwise silently attribute filings to whichever loaded first.
                log.warning(
                    "alias %r claimed by both %s and %s; keeping the first",
                    alias,
                    taken.firm_id,
                    firm.id,
                )
            continue

        session.add(
            FirmAlias(
                firm_id=firm.id,
                alias=alias,
                canonical_alias=canonical,
                alias_type=AliasType.DBA.value,
                confidence=1.0,
                resolution_method=ResolutionMethod.MANUAL.value,
            )
        )
        stats.aliases_created += 1


async def _upsert_source(
    session: AsyncSession,
    *,
    name: str,
    kind: str,
    base_url: str,
    stats: SeedStats,
    firm_id: Any = None,
    locality: str | None = None,
    cadence_hours: int = 24,
    scrape_task: str | None = None,
    config: dict[str, Any] | None = None,
    notes: str | None = None,
) -> Source:
    existing = (
        await session.execute(select(Source).where(Source.base_url == base_url))
    ).scalar_one_or_none()

    fields = {
        "name": name,
        "kind": kind,
        "firm_id": firm_id,
        "locality": locality,
        "cadence_hours": cadence_hours,
        "scrape_task": scrape_task,
        "config": config or {},
        "notes": notes,
    }

    if existing is None:
        source = Source(base_url=base_url, **fields)
        session.add(source)
        await session.flush()
        stats.sources_created += 1
        return source

    for key, value in fields.items():
        setattr(existing, key, value)
    stats.sources_updated += 1
    return existing


def _firm_sources(row: dict[str, Any], website: str) -> list[dict[str, Any]]:
    """Derive per-firm crawl targets from a firm row.

    Careers pages are first-party only. LinkedIn and Indeed prohibit scraping
    in their terms and block it in practice; for regional firms the company's
    own page is also the authoritative, unaggregated source.
    """
    base = website.rstrip("/")
    sources = [
        {
            "name": f"{row['name']} — website",
            "kind": SourceKind.FIRM_SITE.value,
            "base_url": base,
            "scrape_task": "firm_profile",
            "cadence_hours": 168,
        }
    ]

    careers_path = row.get("careers_path")
    if careers_path:
        sources.append(
            {
                "name": f"{row['name']} — careers",
                "kind": SourceKind.CAREERS.value,
                "base_url": f"{base}{careers_path}",
                "scrape_task": "careers",
                "cadence_hours": 72,
                "config": {
                    # Many firm careers pages redirect to an ATS portal. Follow
                    # the link so the actual job listings are fetched, not just
                    # the "see our jobs on Greenhouse" landing page.
                    "follow_links": True,
                    "link_pattern": (
                        r"greenhouse\.io|lever\.co|myworkdayjobs\.com"
                        r"|workday\.com|smartrecruiters\.com|ashbyhq\.com"
                        r"|breezy\.hr|bamboohr\.com|icims\.com"
                    ),
                    "max_links": 1,
                },
            }
        )
    return sources


async def seed_firms(
    session: AsyncSession, *, path: Path | None = None, stats: SeedStats | None = None
) -> SeedStats:
    stats = stats or SeedStats()
    data = _load_yaml(path or FIRMS_FILE)
    rows = data.get("firms") or []
    if not rows:
        raise SeedError("firms.yaml contains no firms")

    validate_firms(rows)

    # Canonical aliases claimed during this run, across all firms.
    seen: set[str] = set()

    for row in rows:
        firm = await _upsert_firm(session, row, stats)
        await _upsert_aliases(session, firm, row, stats, seen)

        for source in _firm_sources(row, row["website"]):
            await _upsert_source(
                session,
                firm_id=firm.id,
                locality=row.get("county"),
                stats=stats,
                **source,
            )

    return stats


async def seed_sources(
    session: AsyncSession, *, path: Path | None = None, stats: SeedStats | None = None
) -> SeedStats:
    stats = stats or SeedStats()
    data = _load_yaml(path or SOURCES_FILE)
    rows = data.get("sources") or []
    validate_sources(rows)

    for row in rows:
        await _upsert_source(
            session,
            name=row["name"],
            kind=row["kind"],
            base_url=row["base_url"],
            locality=row.get("locality"),
            cadence_hours=row.get("cadence_hours", 24),
            scrape_task=row.get("scrape_task"),
            config={
                **(row.get("config") or {}),
                **({"adapter": row["adapter"]} if row.get("adapter") else {}),
            },
            notes=row.get("notes"),
            stats=stats,
        )

    return stats


async def seed_all(
    session: AsyncSession,
    *,
    firms_path: Path | None = None,
    sources_path: Path | None = None,
) -> SeedStats:
    stats = SeedStats()
    await seed_firms(session, path=firms_path, stats=stats)
    await seed_sources(session, path=sources_path, stats=stats)
    return stats
