"""Alert subscriptions, their delivery ledger, and generated digests."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UUIDPrimaryKey, utc_column

if TYPE_CHECKING:
    from app.models.signal import Signal
    from app.models.user import User


class Alert(Base, UUIDPrimaryKey, Timestamped):
    """A standing subscription to a slice of the signal stream."""

    __tablename__ = "alerts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))

    # {"firm_ids": [...], "localities": ["Loudoun"], "signal_types": [...],
    #  "asset_classes": [...], "min_score": 0.6}
    # JSONB because the filter vocabulary grows with the signal vocabulary, and
    # a column per facet would mean a migration per new filter.
    filters: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)

    channel: Mapped[str] = mapped_column(String(20), default="email")
    channel_target: Mapped[str | None] = mapped_column(String(500))

    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    last_fired_at: Mapped[datetime | None] = utc_column()
    fire_count: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="alerts")
    deliveries: Mapped[list[AlertDelivery]] = relationship(
        back_populates="alert", cascade="all, delete-orphan"
    )


class AlertDelivery(Base, UUIDPrimaryKey, Timestamped):
    """One (alert, signal) delivery attempt.

    The unique constraint is the dedupe guarantee. Detection re-runs daily over
    an overlapping window, so without it a user would be re-notified about the
    same land acquisition every morning until it aged out — the single fastest
    way to get an intelligence product muted.
    """

    __tablename__ = "alert_deliveries"

    alert_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), index=True
    )
    signal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), index=True
    )

    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    delivered_at: Mapped[datetime | None] = utc_column()
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    alert: Mapped[Alert] = relationship(back_populates="deliveries")
    signal: Mapped[Signal] = relationship()

    __table_args__ = (UniqueConstraint("alert_id", "signal_id", name="uq_alert_delivery"),)


class Digest(Base, UUIDPrimaryKey, Timestamped):
    """A generated weekly report over the period's top signals."""

    __tablename__ = "digests"

    period_start: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date)

    title: Mapped[str | None] = mapped_column(String(300))
    markdown: Mapped[str] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(1000))

    signal_count: Mapped[int] = mapped_column(Integer, default=0)
    firm_count: Mapped[int] = mapped_column(Integer, default=0)
    # Signal ids that made the cut, so a digest claim can be traced back to its
    # source documents.
    signal_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)

    generated_at: Mapped[datetime | None] = utc_column()

    __table_args__ = (
        UniqueConstraint("period_start", "period_end", name="uq_digest_period"),
        Index("ix_digests_period", "period_start", "period_end"),
    )
