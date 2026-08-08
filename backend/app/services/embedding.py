"""Document chunking and embedding pipeline.

Turns stored RawDocuments into DocumentChunks with vector embeddings, and
embeds Firm profile cards for semantic search.

Two embedding levels, two purposes:
  document_chunks.embedding  — passage-level vectors for RAG
  firms.embedding            — firm-card vectors for "find me industrial
                               developers in Northern Virginia"-style search

Both use the same local sentence-transformers model (no API cost per document).
The dimensions must match the VECTOR(n) column widths in the migrations; they
are set in EmbeddingSettings.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document import DocumentChunk, RawDocument
from app.models.enums import ExtractionStatus
from app.models.firm import Firm

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(settings.embedding.model_name)
    return _model


def _embed(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    vectors: np.ndarray = model.encode(
        texts,
        batch_size=settings.embedding.batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()


def _rough_token_count(text: str) -> int:
    """Approximate token count: 1.3 words per token for English prose."""
    return max(1, int(len(text.split()) / 1.3))


def chunk_text(text: str, page_number: int | None = None) -> list[dict[str, object]]:
    """Split text into overlapping token windows.

    Chunks stay within configured size limits. When the text represents a single
    PDF page (page_number provided), all chunks inherit that page number so
    every retrieved passage can cite a page.
    """
    target = settings.embedding.chunk_tokens
    overlap = settings.embedding.chunk_overlap_tokens
    words = text.split()

    # Approximate words per chunk
    words_per_chunk = int(target * 1.3)
    words_per_overlap = int(overlap * 1.3)
    step = max(1, words_per_chunk - words_per_overlap)

    chunks: list[dict[str, object]] = []
    start = 0
    while start < len(words):
        end = min(start + words_per_chunk, len(words))
        chunk_text_str = " ".join(words[start:end])
        chunks.append(
            {
                "text": chunk_text_str,
                "page_number": page_number,
                "token_count": _rough_token_count(chunk_text_str),
            }
        )
        if end == len(words):
            break
        start += step

    return chunks


def chunk_document(doc: RawDocument) -> list[dict[str, object]]:
    """Chunk a document, splitting PDFs at page boundaries.

    Page boundaries are marked in the cleaned_text by the PDF extractor as
    ``\\n\\n--- Page N ---\\n\\n``. For HTML docs there is no boundary, so the
    whole text is chunked as one logical page.
    """
    text = (doc.cleaned_text or "").strip()
    if not text:
        return []

    # PDF page markers injected by documents/pdf.py's to_markdown()
    import re

    page_pattern = re.compile(r"\n\n---\s*Page\s+(\d+)\s*---\n\n", re.IGNORECASE)
    parts = page_pattern.split(text)

    if len(parts) == 1:
        # HTML or single-page PDF — no boundaries
        return chunk_text(text)

    all_chunks: list[dict[str, object]] = []
    # parts alternates: text, page_num_str, text, page_num_str, ...
    # First element is pre-first-page content (usually empty)
    i = 0
    current_page = 1
    while i < len(parts):
        if i % 2 == 0:
            page_text = parts[i].strip()
            if page_text:
                all_chunks.extend(chunk_text(page_text, page_number=current_page))
        else:
            try:
                current_page = int(parts[i])
            except ValueError:
                pass
        i += 1

    return all_chunks


@dataclass(slots=True)
class EmbedStats:
    documents_processed: int = 0
    chunks_created: int = 0
    firms_embedded: int = 0
    errors: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.documents_processed} docs, {self.chunks_created} chunks, "
            f"{self.firms_embedded} firms embedded, {self.errors} errors"
        )


async def embed_pending_documents(
    session: AsyncSession,
    limit: int | None = None,
) -> EmbedStats:
    """Process RawDocuments with extraction_status='pending' into DocumentChunks.

    Marks each document 'extracted' on success or 'failed' on error. The
    extraction_attempts counter guards against infinite retry on broken docs.
    """
    stats = EmbedStats()

    query = (
        select(RawDocument)
        .where(
            RawDocument.extraction_status == ExtractionStatus.PENDING.value,
            RawDocument.cleaned_text.is_not(None),
            RawDocument.extraction_attempts < 3,
        )
        .order_by(RawDocument.fetched_at)
    )
    if limit:
        query = query.limit(limit)

    docs = list((await session.execute(query)).scalars())
    log.info("embedding %d pending documents", len(docs))

    for doc in docs:
        doc.extraction_attempts += 1
        try:
            raw_chunks = chunk_document(doc)
            if not raw_chunks:
                doc.extraction_status = ExtractionStatus.SKIPPED_EMPTY.value
                stats.skipped += 1
                continue

            texts = [c["text"] for c in raw_chunks]  # type: ignore[index]
            vectors = _embed(texts)

            for idx, (chunk_data, vector) in enumerate(zip(raw_chunks, vectors)):
                chunk = DocumentChunk(
                    document_id=doc.id,
                    firm_id=None,  # populated by _resolve_chunk_firm below
                    chunk_index=idx,
                    page_number=chunk_data.get("page_number"),  # type: ignore[arg-type]
                    text=chunk_data["text"],  # type: ignore[index]
                    token_count=chunk_data.get("token_count"),  # type: ignore[arg-type]
                    embedding=vector,
                )
                session.add(chunk)

            doc.extraction_status = ExtractionStatus.EXTRACTED.value
            doc.processed_at = datetime.now(UTC)
            stats.chunks_created += len(raw_chunks)
        except Exception as exc:  # noqa: BLE001
            log.warning("embedding failed for document %s: %s", doc.id, exc)
            doc.extraction_status = ExtractionStatus.FAILED.value
            doc.extraction_error = str(exc)[:500]
            stats.errors += 1
            continue

        stats.documents_processed += 1

    # Propagate firm_id from source → chunk so RAG can filter by firm without
    # a join. Done in bulk after all chunks are created.
    await _backfill_chunk_firm_ids(session)

    return stats


async def _backfill_chunk_firm_ids(session: AsyncSession) -> None:
    """Set firm_id on chunks whose parent document has a firm-specific source."""
    from sqlalchemy import update

    from app.models.source import Source

    # Chunks whose document belongs to a firm-specific source but has no firm_id
    stmt = (
        update(DocumentChunk)
        .where(
            DocumentChunk.firm_id.is_(None),
        )
        .values(
            firm_id=select(Source.firm_id)
            .join(RawDocument, RawDocument.source_id == Source.id)
            .where(
                RawDocument.id == DocumentChunk.document_id,
                Source.firm_id.is_not(None),
            )
            .correlate(DocumentChunk)
            .scalar_subquery()
        )
    )
    await session.execute(stmt)


def _firm_profile_text(firm: Firm) -> str:
    """Render a firm's data as a short text card for embedding."""
    parts = [firm.name]
    if firm.firm_type and firm.firm_type != "unknown":
        parts.append(firm.firm_type.replace("_", " "))
    if firm.asset_classes:
        parts.append(", ".join(ac.replace("_", " ") for ac in firm.asset_classes))
    if firm.localities:
        parts.append(", ".join(firm.localities[:5]))
    if firm.focus_areas:
        parts.append(", ".join(firm.focus_areas[:4]))
    if firm.description:
        # Cap description contribution to avoid one field dominating the vector
        parts.append(textwrap.shorten(firm.description, width=400, placeholder="…"))
    return ". ".join(parts)


async def embed_firms(session: AsyncSession) -> EmbedStats:
    """Generate embeddings for firms that are missing one.

    Re-embed any firm whose embedding_updated_at is older than 30 days (a
    firm's profile can change as new asset classes are added in firms.yaml).
    """
    from datetime import timedelta

    stats = EmbedStats()
    cutoff = datetime.now(UTC) - timedelta(days=30)

    firms = list(
        (
            await session.execute(
                select(Firm).where(
                    (Firm.embedding.is_(None)) | (Firm.embedding_updated_at < cutoff)
                )
            )
        ).scalars()
    )

    if not firms:
        return stats

    texts = [_firm_profile_text(f) for f in firms]
    vectors = _embed(texts)

    for firm, vector in zip(firms, vectors):
        firm.embedding = vector
        firm.embedding_updated_at = datetime.now(UTC)
        stats.firms_embedded += 1

    return stats
