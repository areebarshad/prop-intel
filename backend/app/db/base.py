"""Declarative base and column conventions shared by every model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit names so Alembic autogenerate emits stable, reversible constraint
# names instead of database-assigned ones. Without this, dropping a constraint
# in a downgrade requires knowing a name Postgres invented.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        ident = getattr(self, "id", None)
        return f"<{type(self).__name__} id={ident}>"


class UUIDPrimaryKey:
    """UUID primary keys.

    Chosen over serial integers because rows are created concurrently by
    scheduled ingest jobs and by the API, and because an ID appearing in a URL
    shouldn't leak how many firms or signals exist.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class Timestamped:
    """created_at / updated_at maintained by the database, not the app.

    server_default means rows written by migrations, raw SQL, or a bulk COPY
    still get correct timestamps.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def utc_column(**kwargs: Any) -> Mapped[datetime]:
    """A timezone-aware timestamp column.

    Everything here is stored in UTC and rendered in ET at the edges; naive
    timestamps mixed with aware ones is the classic way to get an off-by-hours
    bug in a permit filing date.
    """
    return mapped_column(DateTime(timezone=True), **kwargs)
