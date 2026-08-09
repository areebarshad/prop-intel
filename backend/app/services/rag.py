"""Retrieval-Augmented Generation over the PropIntel document corpus.

Two-step process:
  1. Retrieve — embed the user's question and find the most relevant passages
     via pgvector cosine distance.
  2. Generate — send the passages as context to Claude and ask it to answer.

The answer always includes citations so every claim can be traced back to a
source URL and page number. When the corpus has no relevant passages the
service returns an honest "not enough information" reply rather than a
hallucination.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document import DocumentChunk, RawDocument

log = logging.getLogger(__name__)

# Cosine distance threshold: distance = 1 - similarity, so a 0.35 similarity
# floor means we accept passages with distance ≤ 0.65.
DEFAULT_MIN_SIMILARITY = 0.35
DEFAULT_TOP_K = 8
# Hard cap on total context characters sent to the model.
MAX_CONTEXT_CHARS = 12_000

_RAG_SYSTEM_PROMPT = """\
You are PropIntel, a competitive intelligence assistant for Virginia commercial \
real estate. Answer the user's question using ONLY the document passages provided. \
Be specific: cite property addresses, permit numbers, dollar amounts, and dates \
when they appear in the passages. When the passages don't contain enough \
information to answer confidently, say so explicitly rather than speculating. \
Use the register of a senior commercial real estate analyst — precise, direct, \
no fluff.\
"""


@dataclass(slots=True)
class Citation:
    url: str | None
    page_number: int | None
    excerpt: str


@dataclass(slots=True)
class RagAnswer:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    passages_found: int = 0


async def retrieve(
    session: AsyncSession,
    query_vector: list[float],
    *,
    firm_id: UUID | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> list[tuple[DocumentChunk, RawDocument, float]]:
    """Return the top-k most relevant chunks, each paired with its source doc.

    Only chunks with cosine_similarity ≥ min_similarity are returned.
    """
    max_distance = 1.0 - min_similarity

    stmt = (
        select(
            DocumentChunk,
            RawDocument,
            DocumentChunk.embedding.cosine_distance(query_vector).label("distance"),  # type: ignore[attr-defined]
        )
        .join(RawDocument, RawDocument.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.embedding.is_not(None),
            DocumentChunk.embedding.cosine_distance(query_vector) <= max_distance,  # type: ignore[attr-defined]
        )
    )
    if firm_id is not None:
        stmt = stmt.where(DocumentChunk.firm_id == firm_id)

    stmt = stmt.order_by("distance").limit(top_k)

    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1], float(row[2])) for row in rows]


async def ask(
    session: AsyncSession,
    *,
    question: str,
    firm_id: UUID | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> RagAnswer:
    """Answer a question using corpus passages as grounding context.

    Falls back gracefully when LLM is not configured or no relevant passages
    exist.
    """
    from app.services.embedding import async_embed

    query_vector = (await async_embed([question]))[0]
    rows = await retrieve(
        session, query_vector, firm_id=firm_id, top_k=top_k, min_similarity=min_similarity
    )

    if not rows:
        return RagAnswer(
            answer=(
                "I don't have enough information in the current corpus to answer "
                "that question. Try running the ingest pipeline to add more documents."
            ),
            passages_found=0,
        )

    # Build context, respecting the hard char cap so large chunks don't push
    # the question out of the model's window.
    context_parts: list[str] = []
    citations: list[Citation] = []
    total_chars = 0

    for chunk, doc, distance in rows:
        similarity = round(1.0 - distance, 3)
        excerpt = textwrap.shorten(chunk.text, width=400, placeholder="…")
        page_info = f" (p. {chunk.page_number})" if chunk.page_number else ""
        context_parts.append(
            f"[Source: {doc.url}{page_info} | similarity={similarity}]\n{chunk.text}"
        )
        citations.append(
            Citation(url=doc.final_url or doc.url, page_number=chunk.page_number, excerpt=excerpt)
        )
        total_chars += len(chunk.text)
        if total_chars >= MAX_CONTEXT_CHARS:
            break

    context = "\n\n---\n\n".join(context_parts)

    if not settings.llm.is_configured:
        return RagAnswer(
            answer=(
                f"LLM not configured (set PROPINTEL_LLM__API_KEY). "
                f"Found {len(rows)} relevant passages."
            ),
            citations=citations,
            passages_found=len(rows),
        )

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        base_url=settings.llm.base_url,
        api_key=settings.llm.api_key.get_secret_value() if settings.llm.api_key else "ollama",  # type: ignore[union-attr]
        timeout=settings.llm.timeout_seconds,
        max_retries=settings.llm.max_retries,
    )

    user_message = (
        f"Here are the relevant document passages:\n\n{context}\n\n"
        f"Question: {question}"
    )

    try:
        response = await client.chat.completions.create(
            model=settings.llm.smart_model,
            max_tokens=settings.llm.max_tokens,
            messages=[
                {"role": "system", "content": _RAG_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        answer_text = response.choices[0].message.content or "No response generated."
    except Exception as exc:  # noqa: BLE001
        log.warning("RAG LLM call failed: %s", exc)
        answer_text = (
            f"Answer generation failed: {exc}. "
            "Passages above are the best-matching corpus excerpts."
        )

    return RagAnswer(answer=answer_text, citations=citations, passages_found=len(rows))
