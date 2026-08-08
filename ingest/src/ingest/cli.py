"""PropIntel ingest CLI."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

import httpx
import typer
from sqlalchemy import func, select

from app.db.session import session_scope
from app.models.document import RawDocument
from app.models.enums import SourceKind
from app.models.firm import Firm, FirmAlias
from app.models.source import Source
from ingest.crawler import crawl_source
from ingest.runner import BatchStats, mark_source_crawled
from ingest.seeds.loader import SeedError, seed_all

app = typer.Typer(
    add_completion=False,
    help="Ingest Virginia commercial real estate data into PropIntel.",
    no_args_is_help=True,
)

log = logging.getLogger("ingest")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s  %(message)s",
    )


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    _configure_logging(verbose)


@app.command()
def seed() -> None:
    """Load firms.yaml and sources.yaml into the database (idempotent)."""

    async def _run() -> None:
        async with session_scope() as session:
            try:
                stats = await seed_all(session)
            except SeedError as exc:
                typer.secho(f"seed failed: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(1) from exc
            typer.secho(str(stats), fg=typer.colors.GREEN)

    asyncio.run(_run())


@app.command("verify-seeds")
def verify_seeds(
    kind: Annotated[str | None, typer.Option(help="Only check this source kind")] = None,
    timeout: Annotated[float, typer.Option()] = 15.0,
) -> None:
    """Check that every seeded URL is reachable, and report redirects.

    Seed rows are written from general knowledge and marked `verified: false`.
    A wrong domain either fails loudly, or silently crawls an unrelated company
    and attributes its filings to a firm that never made them. This catches
    both before the first crawl.
    """

    async def _run() -> None:
        async with session_scope() as session:
            query = select(Source).where(Source.is_active.is_(True))
            if kind:
                query = query.where(Source.kind == kind)
            sources = list((await session.execute(query)).scalars())

        if not sources:
            typer.echo("no sources to verify; run `seed` first")
            raise typer.Exit(1)

        ok = moved = broken = 0
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": "PropIntel/0.1"},
        ) as client:
            for source in sources:
                try:
                    response = await client.head(source.base_url)
                    if response.status_code >= 400:
                        # Some servers reject HEAD but serve GET.
                        response = await client.get(source.base_url)
                except Exception as exc:  # noqa: BLE001
                    broken += 1
                    typer.secho(
                        f"  BROKEN  {source.name}\n          {source.base_url}\n"
                        f"          {type(exc).__name__}: {exc}",
                        fg=typer.colors.RED,
                    )
                    continue

                final = str(response.url).rstrip("/")
                if response.status_code >= 400:
                    broken += 1
                    typer.secho(
                        f"  {response.status_code}     {source.name}\n          {source.base_url}",
                        fg=typer.colors.RED,
                    )
                elif final != source.base_url.rstrip("/"):
                    moved += 1
                    typer.secho(
                        f"  MOVED   {source.name}\n          {source.base_url}\n       -> {final}",
                        fg=typer.colors.YELLOW,
                    )
                else:
                    ok += 1

        typer.echo("")
        typer.secho(
            f"{ok} ok, {moved} redirected, {broken} broken (of {len(sources)})",
            fg=typer.colors.GREEN if not broken else typer.colors.YELLOW,
        )

    asyncio.run(_run())


async def _crawl_sources(
    kind: str | None, locality: str | None, limit: int | None, dry_run: bool
) -> BatchStats:
    stats = BatchStats()

    async with session_scope() as session:
        query = select(Source).where(Source.is_active.is_(True))
        if kind:
            query = query.where(Source.kind == kind)
        if locality:
            query = query.where(Source.locality == locality)
        sources = [s for s in (await session.execute(query)).scalars() if s.is_due]
        if limit:
            sources = sources[:limit]

        if dry_run:
            for source in sources:
                typer.echo(f"  would crawl  {source.kind:16s}  {source.base_url}")
            typer.echo(f"\n{len(sources)} source(s) due")
            return stats

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": "PropIntel/0.1"},
        ) as client:
            for source in sources:
                source_stats = await crawl_source(session, source, client)
                stats += source_stats
                typer.echo(
                    f"  stored={source_stats.stored} dupes={source_stats.duplicate}"
                    f"  {source.base_url}"
                )

    return stats


def _crawl_command(kind: SourceKind | None) -> None:
    def command(
        locality: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int | None, typer.Option()] = None,
        dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    ) -> None:
        stats = asyncio.run(_crawl_sources(kind.value if kind else None, locality, limit, dry_run))
        if not dry_run:
            typer.secho(str(stats), fg=typer.colors.GREEN)

    return command  # type: ignore[return-value]


@app.command("crawl-firms")
def crawl_firms(
    locality: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Crawl firm websites."""
    stats = asyncio.run(_crawl_sources(SourceKind.FIRM_SITE.value, locality, limit, dry_run))
    if not dry_run:
        typer.secho(str(stats), fg=typer.colors.GREEN)


@app.command()
def careers(
    locality: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Crawl first-party careers pages for hiring signals."""
    stats = asyncio.run(_crawl_sources(SourceKind.CAREERS.value, locality, limit, dry_run))
    if not dry_run:
        typer.secho(str(stats), fg=typer.colors.GREEN)


@app.command()
def zoning(
    locality: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Crawl planning-commission agendas and minutes (mostly PDF)."""
    stats = asyncio.run(_crawl_sources(SourceKind.ZONING_MINUTES.value, locality, limit, dry_run))
    if not dry_run:
        typer.secho(str(stats), fg=typer.colors.GREEN)


@app.command()
def news(
    limit: Annotated[int | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Crawl regional real estate trade press."""
    stats = asyncio.run(_crawl_sources(SourceKind.NEWS.value, None, limit, dry_run))
    if not dry_run:
        typer.secho(str(stats), fg=typer.colors.GREEN)


@app.command()
def permits(
    locality: Annotated[str | None, typer.Option()] = None,
    since: Annotated[
        str | None, typer.Option(help="ISO date; only filings on or after this")
    ] = None,
    limit: Annotated[int, typer.Option(help="Max records per source")] = 500,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Pull permits from open-data endpoints, attribute them, and emit signals."""
    from datetime import date as _date

    from app.services.entity_resolution import EntityResolver
    from app.services.permit_ingest import ingest_permit_records, load_firm_records
    from ingest.apis import get_adapter

    since_date = _date.fromisoformat(since) if since else None

    async def _run() -> None:
        async with session_scope() as session:
            query = select(Source).where(
                Source.is_active.is_(True),
                Source.kind == SourceKind.OPEN_DATA_API.value,
            )
            if locality:
                query = query.where(Source.locality == locality)
            sources = list((await session.execute(query)).scalars())

            resolver = EntityResolver(await load_firm_records(session))
            any_ran = False

            for source in sources:
                config = dict(source.config or {})
                adapter_name = config.get("adapter")
                if not isinstance(adapter_name, str) or not adapter_name:
                    continue
                try:
                    adapter = get_adapter(adapter_name, source.locality or "", config)
                    adapter.validate()
                except (KeyError, ValueError) as exc:
                    typer.secho(f"  skip  {source.name}: {exc}", fg=typer.colors.YELLOW)
                    continue

                any_ran = True
                records = [record async for record in adapter.fetch(since=since_date, limit=limit)]
                typer.echo(f"  {source.name}: {len(records)} records")

                if dry_run:
                    for record in records[:5]:
                        typer.echo(
                            f"      {record.permit_number} | "
                            f"{record.applicant_name_raw or '(no name)'} | "
                            f"{record.permit_type or '-'}"
                        )
                    continue

                stats = await ingest_permit_records(session, records, resolver=resolver)
                await mark_source_crawled(session, source)
                typer.secho(f"      {stats}", fg=typer.colors.GREEN)

            if not any_ran:
                typer.secho(
                    "no open-data source has a usable adapter config; "
                    "fill service_url / dataset_id in sources.yaml",
                    fg=typer.colors.YELLOW,
                )

    asyncio.run(_run())


@app.command()
def extract(
    task: Annotated[
        str | None, typer.Option(help="team | careers | press_release; default all")
    ] = None,
    limit: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Parse stored documents into people, jobs, and projects.

    Runs over `raw_documents` rather than re-fetching, so extraction can be
    re-run after a parser improves without touching a single county server.
    """
    from webscraper_core.fetchers.base import FetchResult
    from webscraper_core.parsers.registry import get_parser

    import ingest.register  # noqa: F401  (registers the real-estate parsers)
    from app.services.entity_resolution import EntityResolver
    from app.services.permit_ingest import load_firm_records
    from app.services.project_ingest import ingest_announcement
    from app.services.roster_ingest import ingest_job_listings, ingest_roster

    # Which stored documents feed which parser. Matching on `scrape_task` rather
    # than on source kind is deliberate: a firm's homepage and its team page are
    # both `firm_site`, and running the roster parser over homepages harvested
    # nav labels and hero text as staff. Only pages registered as team pages are
    # parsed for rosters.
    task_kinds: dict[str, tuple[str, ...]] = {
        "careers": (SourceKind.CAREERS.value,),
        "press_release": (SourceKind.NEWS.value, SourceKind.PRESS_RELEASE.value),
    }
    tasks = [task] if task else ["team", *task_kinds]

    async def _run() -> None:
        async with session_scope() as session:
            resolver = EntityResolver(await load_firm_records(session))
            totals: dict[str, int] = {}

            for task_name in tasks:
                if task_name == "team":
                    predicate = Source.scrape_task == "team"
                elif task_name in task_kinds:
                    predicate = Source.kind.in_(task_kinds[task_name])
                else:
                    typer.secho(f"unknown task {task_name!r}", fg=typer.colors.RED)
                    raise typer.Exit(1)

                query = (
                    select(RawDocument, Source)
                    .join(Source, Source.id == RawDocument.source_id)
                    .where(predicate, RawDocument.raw_text.is_not(None))
                )
                if limit:
                    query = query.limit(limit)
                rows = list((await session.execute(query)).all())

                parser = get_parser(task_name)
                parsed = 0

                for document, source in rows:
                    result = FetchResult(
                        url=document.url,
                        final_url=document.final_url or document.url,
                        status=document.http_status or 200,
                        html=document.raw_text or "",
                    )
                    record = parser.parse(result)
                    if record is None:
                        continue
                    parsed += 1

                    if task_name == "team" and source.firm_id:
                        stats = await ingest_roster(
                            session,
                            firm_id=source.firm_id,
                            members=record.members,
                            source_document_id=document.id,
                        )
                        totals["people"] = totals.get("people", 0) + stats.people_added
                        totals["signals"] = totals.get("signals", 0) + stats.signals_created
                    elif task_name == "careers" and source.firm_id:
                        stats = await ingest_job_listings(
                            session,
                            firm_id=source.firm_id,
                            listings=record.listings,
                            source_document_id=document.id,
                        )
                        totals["jobs"] = totals.get("jobs", 0) + stats.jobs_added
                        totals["signals"] = totals.get("signals", 0) + stats.signals_created
                    elif task_name == "press_release":
                        pstats = await ingest_announcement(
                            session,
                            record,
                            source_document_id=document.id,
                            resolver=resolver,
                        )
                        totals["projects"] = totals.get("projects", 0) + pstats.created
                        totals["signals"] = totals.get("signals", 0) + pstats.signals_created

                    document.extraction_status = "extracted"

                typer.echo(f"  {task_name}: {parsed}/{len(rows)} documents parsed")

            summary = ", ".join(f"{k}={v}" for k, v in sorted(totals.items()))
            typer.secho(summary or "nothing extracted", fg=typer.colors.GREEN)

    asyncio.run(_run())


# Paths that commonly hold a firm's team or leadership page. Tried in order;
# the first that returns a parseable roster wins.
TEAM_PATHS = (
    "/team",
    "/our-team",
    "/leadership",
    "/about/team",
    "/about/leadership",
    "/people",
    "/about-us/team",
    "/who-we-are",
    "/company/leadership",
)


@app.command("discover-team-pages")
def discover_team_pages(
    limit: Annotated[int | None, typer.Option()] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Find each firm's team page and register it as a source.

    Team pages are almost never the homepage, and firms do not publish a
    sitemap convention for them, so the paths are probed directly. Only pages
    that actually parse into a roster are kept — a 200 from a soft-404 catch-all
    would otherwise register a source that never yields anyone.
    """
    from webscraper_core.fetchers.base import FetchResult
    from webscraper_core.parsers.registry import get_parser

    import ingest.register  # noqa: F401

    async def _run() -> None:
        parser = get_parser("team")

        async with session_scope() as session:
            firms = list((await session.execute(select(Firm))).scalars())
            if limit:
                firms = firms[:limit]

            found = 0
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=20.0,
                headers={"User-Agent": "PropIntel/0.1"},
            ) as client:
                for firm in firms:
                    if not firm.website:
                        continue
                    base = firm.website.rstrip("/")

                    for path in TEAM_PATHS:
                        url = f"{base}{path}"
                        try:
                            response = await client.get(url)
                        except Exception:  # noqa: BLE001
                            continue
                        if response.status_code >= 400:
                            continue

                        record = parser.parse(
                            FetchResult(
                                url=url,
                                final_url=str(response.url),
                                status=response.status_code,
                                html=response.text,
                            )
                        )
                        if record is None or not record.members:
                            continue

                        found += 1
                        typer.echo(f"  {firm.name}: {len(record.members)} people at {path}")
                        if not dry_run:
                            exists = (
                                await session.execute(select(Source).where(Source.base_url == url))
                            ).scalar_one_or_none()
                            if exists is None:
                                session.add(
                                    Source(
                                        name=f"{firm.name} — team",
                                        kind=SourceKind.FIRM_SITE.value,
                                        base_url=url,
                                        firm_id=firm.id,
                                        locality=firm.county,
                                        scrape_task="team",
                                        cadence_hours=168,
                                    )
                                )
                        break

            typer.secho(
                f"{found}/{len(firms)} firms have a discoverable team page",
                fg=typer.colors.GREEN,
            )

    asyncio.run(_run())


@app.command()
def status() -> None:
    """Show what is in the database."""

    async def _run() -> None:
        async with session_scope() as session:
            counts = {
                "firms": await session.scalar(select(func.count()).select_from(Firm)),
                "aliases": await session.scalar(select(func.count()).select_from(FirmAlias)),
                "sources": await session.scalar(select(func.count()).select_from(Source)),
                "documents": await session.scalar(select(func.count()).select_from(RawDocument)),
            }
            width = max(len(k) for k in counts)
            for key, value in counts.items():
                typer.echo(f"  {key:<{width}}  {value}")

            failing = list(
                (
                    await session.execute(
                        select(Source)
                        .where(Source.consecutive_failures > 0)
                        .order_by(Source.consecutive_failures.desc())
                        .limit(10)
                    )
                ).scalars()
            )
            if failing:
                typer.echo("\n  failing sources:")
                for source in failing:
                    typer.secho(
                        f"    {source.consecutive_failures}x  {source.name}",
                        fg=typer.colors.YELLOW,
                    )

    asyncio.run(_run())


@app.command()
def embed(
    limit: Annotated[int | None, typer.Option(help="Max documents to embed per run")] = None,
    firms_only: Annotated[bool, typer.Option("--firms-only")] = False,
) -> None:
    """Chunk stored documents and embed them with sentence-transformers.

    Run after crawl commands to populate document_chunks with vector embeddings
    for RAG, and firms.embedding for semantic firm search.
    """
    from app.services.embedding import embed_firms, embed_pending_documents

    async def _run() -> None:
        async with session_scope() as session:
            if not firms_only:
                doc_stats = await embed_pending_documents(session, limit=limit)
                typer.secho(f"documents: {doc_stats}", fg=typer.colors.GREEN)
            firm_stats = await embed_firms(session)
            typer.secho(f"firms: {firm_stats.firms_embedded} embedded", fg=typer.colors.GREEN)

    asyncio.run(_run())


@app.command("enrich-permits")
def enrich_permits(
    locality: Annotated[str, typer.Option()] = "Fairfax",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Attribute unlinked permits using parcel ownership data.

    Joins permits that have a parcel_id but no firm_id against the county
    property records (Fairfax Real Property ArcGIS layer) to infer the
    developer from the owner name.

    The parcel service URL in parcel_enrichment.py must be verified before
    running against live data (run verify-seeds first).
    """
    from app.services.entity_resolution import EntityResolver
    from app.services.parcel_enrichment import enrich_permits_with_parcel_owners
    from app.services.permit_ingest import load_firm_records

    async def _run() -> None:
        async with session_scope() as session:
            if dry_run:
                from app.models.project import Permit

                unlinked = (
                    await session.scalar(
                        select(func.count()).select_from(Permit).where(
                            Permit.firm_id.is_(None),
                            Permit.parcel_id.is_not(None),
                            Permit.locality == locality,
                        )
                    )
                ) or 0
                typer.echo(f"  {unlinked} unattributed {locality} permits with parcel IDs")
                return

            resolver = EntityResolver(await load_firm_records(session))
            stats = await enrich_permits_with_parcel_owners(
                session, resolver, locality=locality
            )
            typer.secho(str(stats), fg=typer.colors.GREEN)

    asyncio.run(_run())


@app.command()
def trends(
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Compute trend windows and emit derived signals (surges, pivots, anomalies)."""
    from app.services.trend_detection import run_trend_detection

    async def _run() -> None:
        async with session_scope() as session:
            if dry_run:
                firms_count = await session.scalar(
                    select(func.count()).select_from(Firm).where(Firm.is_active.is_(True))
                )
                typer.echo(f"  would process {firms_count} firms")
                return
            stats = await run_trend_detection(session)
            typer.secho(str(stats), fg=typer.colors.GREEN)

    asyncio.run(_run())


if __name__ == "__main__":
    app()
