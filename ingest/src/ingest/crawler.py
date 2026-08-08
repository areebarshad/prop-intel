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


def extract_links(html: str, base_url: str, pattern: str = "") -> list[str]:
    """Extract and optionally filter anchor links from an HTML page.

    When *pattern* is given, any URL matching it is included regardless of
    domain — so an ATS URL (boards.greenhouse.io) is reachable from a careers
    page on the firm's own domain. When pattern is absent, only same-domain
    links are returned (prevents accidental spider-trap escapes).
    """
    from selectolax.parser import HTMLParser

    compiled = re.compile(pattern, re.IGNORECASE) if pattern else None
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
) -> str | None:
    """Return raw HTML for the index page, from the stored document when possible."""
    if document_id is not None:
        doc = await session.get(RawDocument, UUID(document_id))
        if doc is not None and doc.raw_text:
            return doc.raw_text
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

    index_error = index_result.error if index_result.outcome is FetchOutcome.FAILED else None
    await mark_source_crawled(session, source, error=index_error)

    if not source.config.get("follow_links"):
        return stats

    html = await _index_html(session, index_result.document_id, source.base_url, client)
    if not html:
        log.warning("no HTML available for link extraction from %s", source.base_url)
        return stats

    pattern = str(source.config.get("link_pattern", ""))
    max_links = int(source.config.get("max_links", 50))
    discovered = extract_links(html, source.base_url, pattern)[:max_links]

    if discovered:
        log.info("%s: following %d discovered links", source.name, len(discovered))

    for url in discovered:
        link_result = await fetch_and_ingest(session, source=source, url=url, client=client)
        stats.record(link_result)

    return stats
