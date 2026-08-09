"""Fetch documents and land them in `raw_documents`.

The contract this module upholds is idempotency. Scheduled crawls re-visit the
same URLs every few hours, and municipal portals routinely republish identical
minutes under a new URL. Without content-hash dedupe every run would re-extract,
re-embed, and re-signal the same content — burning tokens and, worse, firing an
alert about a land acquisition the user was already told about.

Fetching, robots compliance, throttling, and the static/dynamic escalation
ladder all come from `webscraper-core`. This module owns only the persistence
decision: is this content new, and if so what does it look like cleaned.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import RawDocument
from app.models.enums import ContentType, ExtractionStatus
from app.models.source import Source
from ingest.documents.pdf import extract_pages

log = logging.getLogger(__name__)

# RobotsGate instances cached per netloc so robots.txt is not re-fetched on every request.
_robots_gates: dict[str, Any] = {}

# Below this, the "document" is a 404 page, a login wall, or an empty JS shell.
# Storing them costs nothing but pollutes the corpus and wastes an extraction.
MIN_USEFUL_CHARS = 200

# Guards against a misconfigured source pointing at a multi-hundred-megabyte
# archive and exhausting memory mid-crawl.
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024

# Statuses that mean "a WAF refused this client", not "this page is gone".
# Worth retrying through a real browser; a 404 is not.
BOT_BLOCK_STATUSES = frozenset({401, 403, 429})

# Phrases that identify a bot-check interstitial served with HTTP 200. These
# pass the length threshold and would otherwise be stored as real documents —
# making a comprehensively blocked source look like a healthy one that simply
# had nothing to say, which is the worst of both worlds.
CHALLENGE_MARKERS = (
    "performing security verification",
    "checking your browser",
    "enable javascript and cookies to continue",
    "verify you are human",
    "verifying you are human",
    "ddos protection by",
    "attention required! | cloudflare",
    "please enable cookies",
    "request unsuccessful. incapsula",
    "why have i been blocked",
)


def looks_like_bot_challenge(text: str) -> bool:
    """Whether extracted text is an interstitial rather than page content."""
    lowered = text[:2000].lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


class FetchOutcome(StrEnum):
    STORED = "stored"
    DUPLICATE = "duplicate"  # content hash already present
    TOO_SHORT = "too_short"
    BOT_CHALLENGE = "bot_challenge"  # WAF interstitial served with HTTP 200
    SCANNED_PDF = "scanned_pdf"  # no text layer; needs OCR
    ROBOTS_DENIED = "robots_denied"
    FAILED = "failed"


@dataclass(slots=True)
class IngestResult:
    """What happened to one URL. Never raises — a bad URL must not stop a batch."""

    url: str
    outcome: FetchOutcome
    document_id: str | None = None
    content_hash: str | None = None
    error: str | None = None

    @property
    def is_new(self) -> bool:
        return self.outcome is FetchOutcome.STORED


@dataclass(slots=True)
class BatchStats:
    stored: int = 0
    duplicate: int = 0
    skipped: int = 0
    failed: int = 0

    def record(self, result: IngestResult) -> None:
        if result.outcome is FetchOutcome.STORED:
            self.stored += 1
        elif result.outcome is FetchOutcome.DUPLICATE:
            self.duplicate += 1
        elif result.outcome is FetchOutcome.FAILED:
            self.failed += 1
        else:
            self.skipped += 1

    def __iadd__(self, other: BatchStats) -> BatchStats:
        self.stored += other.stored
        self.duplicate += other.duplicate
        self.skipped += other.skipped
        self.failed += other.failed
        return self

    def __str__(self) -> str:
        return (
            f"{self.stored} stored, {self.duplicate} duplicate, "
            f"{self.skipped} skipped, {self.failed} failed"
        )


def content_hash(text: str) -> str:
    """Hash the *cleaned* text, not the raw bytes.

    Raw HTML changes on every request — rotating CSRF tokens, ad slots, session
    ids, a "last updated" stamp — so hashing bytes would mark every re-crawl as
    new content. Hashing extracted text means a page is new only when what it
    says has changed.
    """
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def clean_html_text(html: str, url: str) -> str:
    """Extract readable text from HTML, reusing webscraper-core's cleaner.

    Imported lazily: `trafilatura` pulls in a heavy dependency tree, and the
    API container never calls this path.
    """
    from webscraper_core.fetchers.base import FetchResult
    from webscraper_core.llm.anthropic_client import readable_text

    result = FetchResult(url=url, final_url=url, status=200, html=html)
    text: str = readable_text(result)
    return text


async def persist_document(
    session: AsyncSession,
    *,
    source: Source,
    url: str,
    cleaned_text: str,
    raw_text: str | None = None,
    final_url: str | None = None,
    title: str | None = None,
    content_type: str = ContentType.HTML.value,
    http_status: int | None = None,
    page_count: int | None = None,
    published_at: datetime | None = None,
) -> IngestResult:
    """Store a document unless its content has been seen before.

    The uniqueness check is advisory — the database's unique constraint on
    `content_hash` is what actually guarantees it. Two concurrent crawlers can
    both pass this check; the loser gets an IntegrityError and is treated as a
    duplicate, which is the correct outcome.
    """
    text = (cleaned_text or "").strip()
    if len(text) < MIN_USEFUL_CHARS:
        return IngestResult(url=url, outcome=FetchOutcome.TOO_SHORT)

    if looks_like_bot_challenge(text):
        # Storing this would record a blocked source as a healthy one and put a
        # security-check page into the corpus that RAG could later cite.
        log.warning("bot challenge served for %s; not storing", url)
        return IngestResult(
            url=url,
            outcome=FetchOutcome.BOT_CHALLENGE,
            error="bot challenge interstitial",
        )

    digest = content_hash(text)

    existing = (
        await session.execute(select(RawDocument.id).where(RawDocument.content_hash == digest))
    ).scalar_one_or_none()
    if existing is not None:
        log.debug("duplicate content for %s (hash %s)", url, digest[:12])
        return IngestResult(
            url=url,
            outcome=FetchOutcome.DUPLICATE,
            document_id=str(existing),
            content_hash=digest,
        )

    document = RawDocument(
        source_id=source.id,
        url=url,
        final_url=final_url or url,
        content_hash=digest,
        content_type=content_type,
        http_status=http_status,
        title=title,
        raw_text=raw_text,
        cleaned_text=text,
        page_count=page_count,
        word_count=len(text.split()),
        published_at=published_at,
        fetched_at=datetime.now(UTC),
        extraction_status=ExtractionStatus.PENDING.value,
    )
    try:
        async with session.begin_nested():
            session.add(document)
            await session.flush()
    except Exception as exc:  # noqa: BLE001
        # Almost certainly the unique constraint losing a concurrency race.
        # The savepoint rolls back only this document; the batch transaction survives.
        log.info("could not store %s, treating as duplicate: %s", url, exc)
        return IngestResult(url=url, outcome=FetchOutcome.DUPLICATE, content_hash=digest)

    return IngestResult(
        url=url,
        outcome=FetchOutcome.STORED,
        document_id=str(document.id),
        content_hash=digest,
    )


async def ingest_pdf(
    session: AsyncSession, *, source: Source, url: str, data: bytes
) -> IngestResult:
    """Ingest a PDF that has already been downloaded."""
    try:
        document = extract_pages(data)
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF extraction failed for %s: %s", url, exc)
        return IngestResult(url=url, outcome=FetchOutcome.FAILED, error=str(exc))

    if document.is_scanned:
        # Recorded rather than silently dropped: a locality that switches to
        # scanned agendas must look like a gap to fix, not like a locality that
        # stopped filing anything.
        log.warning("%s has no text layer across %d pages", url, document.page_count)
        return IngestResult(url=url, outcome=FetchOutcome.SCANNED_PDF)

    return await persist_document(
        session,
        source=source,
        url=url,
        cleaned_text=document.to_markdown(),
        raw_text=document.text,
        title=document.metadata.get("title") or None,
        content_type=ContentType.PDF.value,
        page_count=document.page_count,
    )


def _is_bot_block(exc: Exception) -> bool:
    """Whether a failure looks like bot protection rather than a dead page.

    A 403 or 429 from a site that a browser renders fine is the signature of a
    WAF, not of missing content — those are worth a second attempt through a
    real browser. A 404 is not.
    """
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in BOT_BLOCK_STATUSES


async def _fetch_rendered(url: str) -> tuple[str, str, int]:
    """Fetch through headless Chromium. Returns (html, final_url, status).

    Uses webscraper-core's PlaywrightFetcher so the same throttle, user-agent
    rotation, retry policy, and resource blocking apply as on the static path.
    """
    from webscraper_core.config import load_settings
    from webscraper_core.fetchers.router import select_fetcher

    fetcher = select_fetcher(load_settings(), force_dynamic=True)
    try:
        result = await fetcher.fetch(url)
        return result.html, result.final_url, result.status
    finally:
        await fetcher.aclose()


async def fetch_and_ingest(
    session: AsyncSession,
    *,
    source: Source,
    url: str,
    client: httpx.AsyncClient | None = None,
    allow_dynamic: bool = True,
) -> IngestResult:
    """Fetch one URL and persist it, dispatching on content type.

    Static first, then escalate to a real browser when the static result is
    unusable. Two escalation triggers, because they are distinct failures with
    the same fix:

      * the page fetched but rendered almost nothing — a JS application whose
        content arrives after hydration (Stanley Martin, Peterson, JBG Smith);
      * the request was refused with 403/429 — bot protection that a headless
        browser with a real user-agent gets past (Merritt Properties).

    Without this, a fifth of the seeded firms silently produce no documents and
    look inactive rather than unscraped.
    """
    from webscraper_core.utils.robots import RobotsGate

    netloc = urlparse(url).netloc
    if netloc not in _robots_gates:
        _robots_gates[netloc] = RobotsGate(True)
    gate = _robots_gates[netloc]

    allowed = await gate.allowed(url)
    source.robots_allowed = allowed
    source.robots_checked_at = datetime.now(UTC)
    if not allowed:
        log.info("robots.txt disallows %s", url)
        return IngestResult(url=url, outcome=FetchOutcome.ROBOTS_DENIED)

    owns_client = client is None
    client = client or httpx.AsyncClient(
        follow_redirects=True, timeout=30.0, headers={"User-Agent": "PropIntel/0.1"}
    )

    html: str | None = None
    final_url = url
    status: int | None = None
    escalated = False

    try:
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                cl = response.headers.get("content-length")
                if cl and int(cl) > MAX_DOCUMENT_BYTES:
                    return IngestResult(
                        url=url,
                        outcome=FetchOutcome.FAILED,
                        error=f"document exceeds {MAX_DOCUMENT_BYTES} bytes",
                    )
                raw_bytes = bytearray()
                async for chunk in response.aiter_bytes():
                    raw_bytes.extend(chunk)
                    if len(raw_bytes) > MAX_DOCUMENT_BYTES:
                        return IngestResult(
                            url=url,
                            outcome=FetchOutcome.FAILED,
                            error=f"document exceeds {MAX_DOCUMENT_BYTES} bytes",
                        )
                media_type = response.headers.get("content-type", "").split(";")[0].strip()
                encoding = response.charset_encoding or "utf-8"
                final_url = str(response.url)
                status = response.status_code

            if media_type == "application/pdf" or url.lower().endswith(".pdf"):
                # A PDF has no DOM to hydrate; the browser path would add nothing.
                return await ingest_pdf(session, source=source, url=url, data=bytes(raw_bytes))

            html = raw_bytes.decode(encoding, errors="replace")
        except Exception as exc:  # noqa: BLE001
            if not (allow_dynamic and _is_bot_block(exc)):
                log.warning("fetch failed for %s: %s", url, exc)
                return IngestResult(url=url, outcome=FetchOutcome.FAILED, error=str(exc))
            log.info("static fetch blocked for %s; escalating to browser", url)
            escalated = True

        if not escalated and html is not None and allow_dynamic:
            from webscraper_core.fetchers.base import FetchResult
            from webscraper_core.fetchers.router import looks_js_rendered

            probe = FetchResult(url=url, final_url=final_url, status=status or 200, html=html)
            if looks_js_rendered(probe):
                log.info("static under-rendered for %s; escalating to browser", url)
                escalated = True

        if escalated:
            try:
                html, final_url, status = await _fetch_rendered(url)
            except ImportError:
                log.warning(
                    "dynamic fetch unavailable for %s "
                    "(install the 'dynamic' extra and `playwright install chromium`)",
                    url,
                )
                if html is None:
                    return IngestResult(
                        url=url,
                        outcome=FetchOutcome.FAILED,
                        error="dynamic fetch unavailable",
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("browser fetch failed for %s: %s", url, exc)
                if html is None:
                    return IngestResult(url=url, outcome=FetchOutcome.FAILED, error=str(exc))

        if html is None:
            return IngestResult(url=url, outcome=FetchOutcome.FAILED, error="no content fetched")

        return await persist_document(
            session,
            source=source,
            url=url,
            cleaned_text=clean_html_text(html, url),
            raw_text=html,
            final_url=final_url,
            http_status=status,
        )
    except Exception as exc:  # noqa: BLE001  (collected, never fatal to a batch)
        log.warning("ingest failed for %s: %s", url, exc)
        return IngestResult(url=url, outcome=FetchOutcome.FAILED, error=str(exc))
    finally:
        if owns_client:
            await client.aclose()


async def mark_source_crawled(
    session: AsyncSession, source: Source, *, error: str | None = None
) -> None:
    """Record the crawl outcome so a persistently broken source is visible.

    Without this a source that started 404ing looks identical to a source with
    nothing new to report.
    """
    now = datetime.now(UTC)
    source.last_crawled_at = now
    if error is None:
        source.last_success_at = now
        source.consecutive_failures = 0
        source.last_error = None
    else:
        source.consecutive_failures += 1
        source.last_error = error[:2000]
