"""People at firms, and the job postings that signal where a firm is growing."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UUIDPrimaryKey, utc_column

if TYPE_CHECKING:
    from app.models.document import RawDocument
    from app.models.firm import Firm


class Person(Base, UUIDPrimaryKey, Timestamped):
    """An executive, broker, or developer associated with a firm.

    Rows come from diffing a firm's team page across crawls: a name that appears
    where it wasn't is a hire, one that disappears is a departure. Both are
    material competitive signals — a developer hiring a head of industrial
    acquisitions is telling you where they're going before any permit is filed.
    """

    __tablename__ = "people"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), index=True)
    canonical_name: Mapped[str] = mapped_column(String(200), index=True)
    title: Mapped[str | None] = mapped_column(String(300))
    role_type: Mapped[str | None] = mapped_column(String(30), index=True)

    email: Mapped[str | None] = mapped_column(String(320))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    bio: Mapped[str | None] = mapped_column(Text)

    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(default=True, index=True)

    # When the team page first showed and last showed this person. These are
    # observations, not employment facts: `first_seen_at` is an upper bound on
    # the actual hire date, which matters when reporting "recent hires".
    first_seen_at: Mapped[datetime | None] = utc_column()
    last_seen_at: Mapped[datetime | None] = utc_column()

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="SET NULL")
    )

    firm: Mapped[Firm] = relationship(back_populates="people")
    source_document: Mapped[RawDocument | None] = relationship()

    __table_args__ = (
        UniqueConstraint("firm_id", "canonical_name", name="uq_person_firm_name"),
        Index("ix_people_firm_current", "firm_id", "is_current"),
    )


class JobPosting(Base, UUIDPrimaryKey, Timestamped):
    """An open role scraped from a firm's own careers page.

    Only first-party career pages — LinkedIn and Indeed prohibit scraping in
    their terms and actively block it. For 100 regional firms the company's own
    page is also the higher-signal source: it's authoritative and unaggregated.

    Open-posting count over time is the input to the hiring-surge anomaly.
    """

    __tablename__ = "job_postings"

    firm_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    department: Mapped[str | None] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(200), index=True)
    employment_type: Mapped[str | None] = mapped_column(String(60))
    url: Mapped[str | None] = mapped_column(String(1000))
    raw_text: Mapped[str | None] = mapped_column(Text)

    posted_date: Mapped[date | None] = mapped_column(Date)
    # first_seen/last_seen are crawl observations. A posting missing from the
    # current crawl is closed, which is how `closed_at` gets set — the site
    # never tells us directly.
    first_seen_at: Mapped[datetime | None] = utc_column(index=True)
    last_seen_at: Mapped[datetime | None] = utc_column()
    closed_at: Mapped[datetime | None] = utc_column()
    is_open: Mapped[bool] = mapped_column(default=True, index=True)

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="SET NULL")
    )

    firm: Mapped[Firm] = relationship(back_populates="job_postings")
    source_document: Mapped[RawDocument | None] = relationship()

    __table_args__ = (
        # Same title in two cities is two roles; same title and city re-seen on
        # the next crawl is one role. The URL would be more precise but many
        # career pages reuse or omit it.
        UniqueConstraint("firm_id", "title", "location", name="uq_job_firm_title_loc"),
        Index("ix_job_postings_firm_open", "firm_id", "is_open"),
    )
