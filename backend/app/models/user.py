"""Users and API keys.

Built in Phase 2 rather than deferred to monetization: retrofitting auth across
a live API surface means touching every route and every client at once. Tiers
and per-key rate limits exist from the first migration so Phase 4 is a Stripe
integration, not a schema change.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UUIDPrimaryKey, utc_column

if TYPE_CHECKING:
    from app.models.alert import Alert


class User(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200))

    tier: Mapped[str] = mapped_column(String(20), default="free", index=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_superuser: Mapped[bool] = mapped_column(default=False)
    last_login_at: Mapped[datetime | None] = utc_column()

    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    alerts: Mapped[list[Alert]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ApiKey(Base, UUIDPrimaryKey, Timestamped):
    """A programmatic credential.

    Only the hash is stored — the plaintext key is shown once at creation and is
    unrecoverable afterward. `key_prefix` is the displayable fragment so a user
    can tell two keys apart in a list without the secret being retrievable.
    """

    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(20), index=True)

    # Copied from the user's tier at issue time so a key can be scoped
    # independently — an integration key can stay on `free` limits while the
    # owning account is `enterprise`.
    tier: Mapped[str] = mapped_column(String(20), default="free")
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=20)

    last_used_at: Mapped[datetime | None] = utc_column()
    expires_at: Mapped[datetime | None] = utc_column()
    revoked_at: Mapped[datetime | None] = utc_column()

    user: Mapped[User] = relationship(back_populates="api_keys")

    @property
    def is_valid(self) -> bool:
        from datetime import UTC
        from datetime import datetime as dt

        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > dt.now(UTC)
