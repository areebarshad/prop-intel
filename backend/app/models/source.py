"""Crawl targets: firm sites, permit portals, minutes archives, open-data APIs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UUIDPrimaryKey, utc_column

if TYPE_CHECKING:
    from app.models.document import RawDocument
    from app.models.firm import Firm


class Source(Base, UUIDPrimaryKey, Timestamped):
    """One crawlable or queryable origin of documents."""

    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    base_url: Mapped[str] = mapped_column(String(1000), unique=True)

    # Set for firm-specific sources (a company's team page); null for
    # jurisdiction-wide ones (a county permit portal).
    firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), index=True
    )
    locality: Mapped[str | None] = mapped_column(String(120), index=True)

    # Which registered webscraper-core task parses this source.
    scrape_task: Mapped[str | None] = mapped_column(String(40))
    # Free-form per-adapter settings: ArcGIS layer ids, Socrata dataset ids,
    # crawl depth, CSS selectors. Kept as JSONB so adding a locality doesn't
    # require a migration.
    config: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)

    cadence_hours: Mapped[int] = mapped_column(Integer, default=24)

    # Recorded from the live robots.txt check, so a source that starts
    # disallowing crawlers is visible rather than silently failing every run.
    robots_allowed: Mapped[bool | None] = mapped_column()
    robots_checked_at: Mapped[datetime | None] = utc_column()

    last_crawled_at: Mapped[datetime | None] = utc_column(index=True)
    last_success_at: Mapped[datetime | None] = utc_column()
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    firm: Mapped[Firm | None] = relationship()
    documents: Mapped[list[RawDocument]] = relationship(back_populates="source")

    @property
    def is_due(self) -> bool:
        """True when this source hasn't been crawled within its cadence."""
        if not self.is_active or self.robots_allowed is False:
            return False
        if self.last_crawled_at is None:
            return True
        from datetime import UTC, timedelta

        age = datetime.now(UTC) - self.last_crawled_at
        return age >= timedelta(hours=self.cadence_hours)
