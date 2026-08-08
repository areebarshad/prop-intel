"""Firms, their aliases, and inferred relationships between them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base, Timestamped, UUIDPrimaryKey, utc_column

if TYPE_CHECKING:
    from app.models.document import RawDocument
    from app.models.person import JobPosting, Person
    from app.models.project import Permit, PropertyProject
    from app.models.signal import Signal

EMBEDDING_DIM = settings.embedding.dimensions


class Firm(Base, UUIDPrimaryKey, Timestamped):
    """A real estate developer, brokerage, investor, or property manager."""

    __tablename__ = "firms"

    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)

    # Lowercased, punctuation-stripped, entity-suffix-free form of `name`.
    # Entity resolution matches against this, never the display name.
    canonical_name: Mapped[str] = mapped_column(String(300), index=True)

    website: Mapped[str | None] = mapped_column(String(500))
    firm_type: Mapped[str] = mapped_column(String(40), default="unknown", index=True)

    hq_address: Mapped[str | None] = mapped_column(String(400))
    city: Mapped[str | None] = mapped_column(String(120), index=True)
    county: Mapped[str | None] = mapped_column(String(120), index=True)
    state: Mapped[str] = mapped_column(String(2), default="VA")
    postal_code: Mapped[str | None] = mapped_column(String(12))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(String(40), index=True)

    # Filter facets for firm search.
    focus_areas: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    asset_classes: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    localities: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list)

    employee_count: Mapped[int | None] = mapped_column(Integer)
    pipeline_project_count: Mapped[int] = mapped_column(Integer, default=0)
    founded_year: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)

    # Embedding of a rendered profile card (name, focus, asset classes,
    # localities, description). Powers semantic firm search; the chunk-level
    # embeddings on document_chunks power RAG. Two levels, two purposes.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_updated_at: Mapped[datetime | None] = utc_column()

    first_seen_at: Mapped[datetime | None] = utc_column()
    last_activity_at: Mapped[datetime | None] = utc_column(index=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    aliases: Mapped[list[FirmAlias]] = relationship(
        back_populates="firm", cascade="all, delete-orphan"
    )
    projects: Mapped[list[PropertyProject]] = relationship(back_populates="firm")
    permits: Mapped[list[Permit]] = relationship(back_populates="firm")
    people: Mapped[list[Person]] = relationship(back_populates="firm")
    job_postings: Mapped[list[JobPosting]] = relationship(back_populates="firm")
    signals: Mapped[list[Signal]] = relationship(back_populates="firm")

    __table_args__ = (
        Index("ix_firms_focus_areas", "focus_areas", postgresql_using="gin"),
        Index("ix_firms_asset_classes", "asset_classes", postgresql_using="gin"),
        Index("ix_firms_localities", "localities", postgresql_using="gin"),
        CheckConstraint("lat IS NULL OR (lat BETWEEN -90 AND 90)", name="lat_range"),
        CheckConstraint("lon IS NULL OR (lon BETWEEN -180 AND 180)", name="lon_range"),
    )


class FirmAlias(Base, UUIDPrimaryKey, Timestamped):
    """Alternate names that resolve to a firm.

    The workhorse of entity resolution. A permit filed by
    "Reston Station Phase III LLC" is only useful once it links to Comstock, and
    the link has to survive the next 40 single-purpose LLCs that firm creates.
    Every accepted fuzzy or LLM match writes a row here, so the second encounter
    with a name is an exact lookup rather than another paid adjudication.
    """

    __tablename__ = "firm_aliases"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(300), index=True)
    canonical_alias: Mapped[str] = mapped_column(String(300), index=True)
    alias_type: Mapped[str] = mapped_column(String(30), default="llc_holding")

    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    resolution_method: Mapped[str] = mapped_column(String(20), default="manual")
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="SET NULL")
    )

    firm: Mapped[Firm] = relationship(back_populates="aliases")
    source_document: Mapped[RawDocument | None] = relationship()

    __table_args__ = (
        # One canonical alias maps to one firm. A second firm claiming the same
        # alias is a resolution conflict that must surface, not be absorbed.
        UniqueConstraint("canonical_alias", name="uq_firm_aliases_canonical_alias"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
    )


class FirmRelationship(Base, UUIDPrimaryKey, Timestamped):
    """An inferred edge between two firms. Powers competitor mapping."""

    __tablename__ = "firm_relationships"

    firm_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), index=True
    )
    firm_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(40), index=True)

    weight: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_count: Mapped[int] = mapped_column(Integer, default=1)
    evidence_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)

    first_detected_at: Mapped[datetime | None] = utc_column()
    last_detected_at: Mapped[datetime | None] = utc_column()

    firm_a: Mapped[Firm] = relationship(foreign_keys=[firm_a_id])
    firm_b: Mapped[Firm] = relationship(foreign_keys=[firm_b_id])

    __table_args__ = (
        UniqueConstraint("firm_a_id", "firm_b_id", "relation_type", name="uq_firm_relationship"),
        # Edges are undirected. Storing them with firm_a_id < firm_b_id (enforced
        # by the writer) keeps one row per pair instead of two mirrored rows that
        # can drift apart.
        CheckConstraint("firm_a_id <> firm_b_id", name="no_self_relation"),
        CheckConstraint("weight BETWEEN 0 AND 1", name="weight_range"),
    )
