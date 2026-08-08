"""Depth-aware source crawler.

Phase 1 fetched exactly one URL per source — the base_url. That leaves
job_postings and property_projects empty: careers pages that redirect to ATS
portals, and news category indexes that need one hop to individual articles.

This module generalises the fetch loop. When a source has ``follow_links: true``
in its config, the crawler fetches the index URL, extracts links matching
``link_pattern``, and fetches each discovered URL under the same source.

Config keys (all optional):
  follow_links   bool  — enable link-following (default false)
  link_pattern   str   — regex filter on full URLs; absent = same-domain links
  max_links      int   — cap on discovered links per crawl (default 50)
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import RawDocument
from app.models.source import Source
from ingest.runner import BatchStats, FetchOutcome, fetch_and_ingest, mark_source_crawled

log = logging.getLogger(__name__)

# Outcomes that should propagate as errors to mark_source_crawled. Any outcome
# not in this set (STORED, DUPLICATE, SCANNED_PDF) is considered non-critical.
_BAD_OUTCOMES = frozenset({
    FetchOutcome.FAILED,
    FetchOutcome.BOT_CHALLENGE,
    FetchOutcome.ROBOTS_DENIED,
    FetchOutcome.TOO_SHORT,
})


def extract_links(html: str, base_url: str, pattern: str = "") -> list[str]:
    """Extract and optionally filter anchor links from an HTML page.

    When *pattern* is given, any URL matching it is included regardless of
    domain — so an ATS URL (boards.greenhouse.io) is reachable from a careers
    page on the firm's own domain. When pattern is absent, only same-domain
    links are returned (prevents accidental spider-trap escapes).
    """
    from selectolax.parser import HTMLParser

    try:
        compiled = re.compile(pattern, re.IGNORECASE) if pattern else None
    except re.error as exc:
        log.warning("invalid link_pattern %r: %s — skipping link extraction", pattern, exc)
        return []

    base_netloc = urlparse(base_url).netloc
    base_stripped = base_url.rstrip("/")

    seen: set[str] = set()
    links: list[str] = []

    for node in HTMLParser(html).css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base_url, href).rstrip("/")
        if urlparse(url).scheme not in ("http", "https"):
            continue
        if url == base_stripped:
            continue

        if compiled:
            if compiled.search(url) and url not in seen:
                seen.add(url)
                links.append(url)
        else:
            if urlparse(url).netloc == base_netloc and url not in seen:
                seen.add(url)
                links.append(url)

    return links


async def _index_html(
    session: AsyncSession,
    document_id: str | None,
    base_url: str,
    client: httpx.AsyncClient,
    *,
    index_outcome: FetchOutcome | None = None,
) -> str | None:
    """Return raw HTML for the index page, from the stored document when possible.

    Never re-fetches if the index outcome was ROBOTS_DENIED — that would bypass
    the robots.txt gate that fetch_and_ingest enforces.
    """
    if document_id is not None:
        doc = await session.get(RawDocument, UUID(document_id))
        if doc is not None and doc.raw_text:
            return doc.raw_text

    # Do not re-fetch if robots.txt denied the request — doing so bypasses the gate.
    if index_outcome is FetchOutcome.ROBOTS_DENIED:
        log.debug("robots.txt denied %s; skipping fallback re-fetch for link extraction", base_url)
        return None

    # Fallback: lightweight re-fetch for link extraction only
    try:
        resp = await client.get(base_url)
        if resp.status_code < 400:
            return resp.text
    except Exception:  # noqa: BLE001
        pass
    return None


async def crawl_source(
    session: AsyncSession,
    source: Source,
    client: httpx.AsyncClient,
) -> BatchStats:
    """Fetch source.base_url, follow configured links, and mark source crawled.

    mark_source_crawled is called here so cli.py does not need to duplicate the
    error-propagation logic. The returned BatchStats cover the index fetch plus
    all discovered-link fetches.
    """
    stats = BatchStats()

    index_result = await fetch_and_ingest(
        session, source=source, url=source.base_url, client=client
    )
    stats.record(index_result)

    # Propagate any non-success outcome (BOT_CHALLENGE, ROBOTS_DENIED, etc.)
    # so the source health record reflects reality, not just hard FAILED.
    index_error = index_result.error if index_result.outcome in _BAD_OUTCOMES else None
    await mark_source_crawled(session, source, error=index_error)

    if not source.config.get("follow_links"):
        return stats

    html = await _index_html(
        session,
        index_result.document_id,
        source.base_url,
        client,
        index_outcome=index_result.outcome,
    )
    if not html:
        log.warning("no HTML available for link extraction from %s", source.base_url)
        return stats

    raw_pattern = str(source.config.get("link_pattern", ""))
    max_links = int(source.config.get("max_links", 50))
    discovered = extract_links(html, source.base_url, raw_pattern)[:max_links]

    if not discovered:
        return stats

    log.info("%s: following %d discovered links", source.name, len(discovered))

    for url in discovered:
        link_result = await fetch_and_ingest(session, source=source, url=url, client=client)
        stats.record(link_result)

    return stats
