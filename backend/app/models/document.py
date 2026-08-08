"""The raw document layer and its embedded chunks.

Every downstream fact — a permit, a project, a hire, a signal — carries a
foreign key back to a RawDocument. That provenance chain is what lets the
product answer "how do you know?" with a URL and a page number instead of a
shrug, and it is why the raw layer is append-only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base, Timestamped, UUIDPrimaryKey, utc_column

if TYPE_CHECKING:
    from app.models.firm import Firm
    from app.models.source import Source

EMBEDDING_DIM = settings.embedding.dimensions


class RawDocument(Base, UUIDPrimaryKey, Timestamped):
    """One fetched artifact: an HTML page, a PDF, or an API response."""

    __tablename__ = "raw_documents"

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2000))
    final_url: Mapped[str | None] = mapped_column(String(2000))

    # sha256 of the cleaned text. The dedupe key: a portal that republishes the
    # same minutes under a new URL every week would otherwise re-extract, re-embed,
    # and re-signal the same content on every run.
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    content_type: Mapped[str] = mapped_column(String(10), default="html")
    http_status: Mapped[int | None] = mapped_column(Integer)

    title: Mapped[str | None] = mapped_column(String(600))
    raw_text: Mapped[str | None] = mapped_column(Text)
    cleaned_text: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    word_count: Mapped[int | None] = mapped_column(Integer)

    published_at: Mapped[datetime | None] = utc_column(index=True)
    fetched_at: Mapped[datetime | None] = utc_column(index=True)
    processed_at: Mapped[datetime | None] = utc_column()

    extraction_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    extraction_error: Mapped[str | None] = mapped_column(Text)
    # Guards against an infinite retry loop on a document that will never parse.
    extraction_attempts: Mapped[int] = mapped_column(Integer, default=0)

    source: Mapped[Source] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_raw_documents_status_fetched", "extraction_status", "fetched_at"),)


class DocumentChunk(Base, UUIDPrimaryKey, Timestamped):
    """An embedded passage of a document.

    Chunks never span a page boundary. That costs a little recall on facts that
    straddle a page break, and buys the ability to cite "p. 14" in every RAG
    answer — which is the difference between a usable intelligence product and a
    plausible-sounding one.
    """

    __tablename__ = "document_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="CASCADE"), index=True
    )
    # Denormalized from the resolved document so RAG can filter by firm without
    # a join on every vector query.
    firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="SET NULL"), index=True
    )

    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    document: Mapped[RawDocument] = relationship(back_populates="chunks")
    firm: Mapped[Firm | None] = relationship()

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
    )


class EntityCandidate(Base, UUIDPrimaryKey, Timestamped):
    """A mention the resolver could not confidently link to a firm.

    Deliberately a queue rather than a best guess. A wrong link silently
    attributes a competitor's land acquisition to the wrong developer, which is
    worse for the user than a gap they can see.
    """

    __tablename__ = "entity_candidates"

    mention: Mapped[str] = mapped_column(String(400), index=True)
    canonical_mention: Mapped[str] = mapped_column(String(400), index=True)
    context: Mapped[str | None] = mapped_column(Text)

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="CASCADE")
    )
    # What the mention came from ("permit", "zoning_minutes"), and the row id,
    # so an accepted match can be applied back to the originating record.
    origin_table: Mapped[str | None] = mapped_column(String(60))
    origin_id: Mapped[uuid.UUID | None] = mapped_column()

    # Ranked shortlist: [{"firm_id": ..., "name": ..., "score": 0.88}, ...]
    suggestions: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    best_score: Mapped[float | None] = mapped_column(Float)

    status: Mapped[str] = mapped_column(String(20), default="ambiguous", index=True)
    resolved_firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = utc_column()
    resolved_by: Mapped[str | None] = mapped_column(String(120))

    source_document: Mapped[RawDocument | None] = relationship()
    resolved_firm: Mapped[Firm | None] = relationship()

    __table_args__ = (
        # One open review item per distinct mention; re-encountering the same
        # LLC on the next crawl should not add another row to the queue.
        UniqueConstraint("canonical_mention", name="uq_entity_candidate_mention"),
    )
