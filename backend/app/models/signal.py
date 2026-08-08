"""The unified event stream.

Alerts, the weekly digest, the firm timeline, and the dashboard feed all read
this one table. Keeping a single stream — rather than each feature querying
permits, people, and projects separately — means a new signal type is picked up
by every surface at once, and "what happened at this firm this quarter" is one
indexed query rather than a five-way union.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UUIDPrimaryKey, utc_column

if TYPE_CHECKING:
    from app.models.document import RawDocument
    from app.models.firm import Firm


class Signal(Base, UUIDPrimaryKey, Timestamped):
    """One material event attributed to a firm."""

    __tablename__ = "signals"

    firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), index=True
    )
    signal_type: Mapped[str] = mapped_column(String(40), index=True)

    title: Mapped[str] = mapped_column(String(400))
    # One sentence, model-written, in the register a broker would use:
    # "Comstock filed for a 240-unit multifamily building in Reston."
    summary: Mapped[str | None] = mapped_column(Text)

    # 0-1 importance, used to rank the digest and to threshold alerts so a user
    # watching a busy county isn't paged for every routine electrical permit.
    score: Mapped[float] = mapped_column(Float, default=0.5, index=True)
    locality: Mapped[str | None] = mapped_column(String(120), index=True)
    asset_class: Mapped[str | None] = mapped_column(String(40), index=True)

    # When the event happened in the world vs. when the pipeline noticed it.
    # Both matter: the digest orders by occurrence, alert dedupe keys on detection.
    occurred_at: Mapped[datetime | None] = utc_column(index=True)
    detected_at: Mapped[datetime | None] = utc_column(index=True)

    # Type-specific detail: parcel ids, unit counts, the person hired, the
    # constituent signal ids behind a derived trend. JSONB so a new signal type
    # doesn't require a migration.
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)

    # False for signals extracted from a single document; True for those
    # computed by trend and anomaly detection over a window of other signals.
    is_derived: Mapped[bool] = mapped_column(default=False, index=True)

    # Natural key for idempotency: re-running extraction over the same document
    # must not create a second copy of the same event.
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="SET NULL")
    )

    firm: Mapped[Firm | None] = relationship(back_populates="signals")
    source_document: Mapped[RawDocument | None] = relationship()

    __table_args__ = (
        Index("ix_signals_firm_occurred", "firm_id", "occurred_at"),
        Index("ix_signals_type_occurred", "signal_type", "occurred_at"),
        # The alert fan-out query: recent, important, in a watched locality.
        Index("ix_signals_detected_score", "detected_at", "score"),
        CheckConstraint("score BETWEEN 0 AND 1", name="score_range"),
    )


class TrendWindow(Base, UUIDPrimaryKey, Timestamped):
    """A materialized rolling-window aggregate per firm, locality, and period.

    Trend detection needs to compare this quarter's asset-class mix against the
    trailing baseline. Recomputing that from raw signals on every run is fine at
    100 firms and quadratic at 1,000, so the windows are stored and the detector
    reads them.
    """

    __tablename__ = "trend_windows"

    firm_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("firms.id", ondelete="CASCADE"), index=True
    )
    locality: Mapped[str | None] = mapped_column(String(120), index=True)
    period_start: Mapped[datetime] = utc_column(index=True)
    period_end: Mapped[datetime] = utc_column()

    permit_count: Mapped[int] = mapped_column(default=0)
    valuation_sum_usd: Mapped[float] = mapped_column(Float, default=0.0)
    open_job_count: Mapped[int] = mapped_column(default=0)
    news_mention_count: Mapped[int] = mapped_column(default=0)
    new_project_count: Mapped[int] = mapped_column(default=0)

    # {"multifamily": 0.6, "industrial": 0.4} — the distribution whose shift
    # across firms in a region is what "pivot" means.
    asset_class_mix: Mapped[dict[str, float]] = mapped_column(JSONB, default=dict)

    anomaly_score: Mapped[float | None] = mapped_column(Float)

    firm: Mapped[Firm | None] = relationship()

    __table_args__ = (
        UniqueConstraint("firm_id", "locality", "period_start", name="uq_trend_window_period"),
    )
