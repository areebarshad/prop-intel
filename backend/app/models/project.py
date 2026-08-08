"""Development projects and the permit filings that evidence them.

These are deliberately two tables. A permit is a *filing event* — one row per
application, immutable once recorded, and the thing a county publishes. A
project is an *entity* that accretes over years: rezoning, then site plan, then
building permits, then delivery. Collapsing them would either lose filing
history or duplicate project attributes across every filing.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.document import RawDocument
    from app.models.firm import Firm


class PropertyProject(Base, UUIDPrimaryKey, Timestamped):
    """A development project tracked across its lifecycle."""

    __tablename__ = "property_projects"

    firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(400), index=True)
    project_type: Mapped[str | None] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)

    locality: Mapped[str | None] = mapped_column(String(120), index=True)
    address: Mapped[str | None] = mapped_column(String(500))
    parcel_ids: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)

    acreage: Mapped[float | None] = mapped_column(Float)
    unit_count: Mapped[int | None] = mapped_column(Integer)
    square_feet: Mapped[int | None] = mapped_column(Integer)
    # Numeric, not float: these feed valuation sums in trend detection, and
    # float drift across thousands of permits produces visibly wrong totals.
    est_value_usd: Mapped[float | None] = mapped_column(Numeric(14, 2))

    announced_date: Mapped[date | None] = mapped_column(Date, index=True)
    expected_completion: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="SET NULL")
    )

    firm: Mapped[Firm | None] = relationship(back_populates="projects")
    source_document: Mapped[RawDocument | None] = relationship()
    permits: Mapped[list[Permit]] = relationship(back_populates="project")

    __table_args__ = (
        Index("ix_property_projects_parcel_ids", "parcel_ids", postgresql_using="gin"),
        Index("ix_property_projects_geo", "lat", "lon"),
        CheckConstraint("lat IS NULL OR (lat BETWEEN -90 AND 90)", name="lat_range"),
        CheckConstraint("lon IS NULL OR (lon BETWEEN -180 AND 180)", name="lon_range"),
    )


class Permit(Base, UUIDPrimaryKey, Timestamped):
    """A single permit or land-use application as filed with a locality."""

    __tablename__ = "permits"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("property_projects.id", ondelete="SET NULL"), index=True
    )
    firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="SET NULL"), index=True
    )

    locality: Mapped[str] = mapped_column(String(120), index=True)
    permit_number: Mapped[str] = mapped_column(String(80), index=True)
    permit_type: Mapped[str | None] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text)

    # Preserved exactly as filed. Never overwritten by the resolved firm name —
    # if a resolution is later shown to be wrong, this is the ground truth that
    # allows re-running the resolver.
    applicant_name_raw: Mapped[str | None] = mapped_column(String(400), index=True)
    owner_name_raw: Mapped[str | None] = mapped_column(String(400))
    contractor_name_raw: Mapped[str | None] = mapped_column(String(400))

    address: Mapped[str | None] = mapped_column(String(500))
    parcel_id: Mapped[str | None] = mapped_column(String(60), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)

    valuation_usd: Mapped[float | None] = mapped_column(Numeric(14, 2))
    filed_date: Mapped[date | None] = mapped_column(Date, index=True)
    issued_date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str | None] = mapped_column(String(60))

    resolution_method: Mapped[str | None] = mapped_column(String(20))
    resolution_confidence: Mapped[float | None] = mapped_column(Float)

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="SET NULL")
    )

    project: Mapped[PropertyProject | None] = relationship(back_populates="permits")
    firm: Mapped[Firm | None] = relationship(back_populates="permits")
    source_document: Mapped[RawDocument | None] = relationship()

    __table_args__ = (
        # Permit numbers are unique per locality, not globally — Fairfax and
        # Henrico both issue a "B-2026-0001".
        UniqueConstraint("locality", "permit_number", name="uq_permit_locality_number"),
        Index("ix_permits_firm_filed", "firm_id", "filed_date"),
        Index("ix_permits_locality_filed", "locality", "filed_date"),
    )
