"""Marketing-site leads.

Rows are written by the public contact endpoint, so the table is deliberately
independent of `users`: a demo request is not an account, and requiring one
would lose the submission. `created_at` is indexed because the only queries
this table serves are "newest first" and "everything since <date>".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class Lead(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "leads"

    full_name: Mapped[str] = mapped_column(String(200))
    # 320 = the RFC 5321 maximum, matching users.email so the same address is
    # storable in both tables.
    work_email: Mapped[str] = mapped_column(String(320), index=True)
    company: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(200))
    use_case_notes: Mapped[str | None] = mapped_column(Text())

    # Kept for abuse triage on an unauthenticated endpoint. 45 characters is a
    # full IPv6 address including an IPv4-mapped suffix.
    ip_address: Mapped[str | None] = mapped_column(String(45))

    # Overrides Timestamped only to add the index the migration creates.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
