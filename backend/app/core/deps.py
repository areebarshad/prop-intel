"""FastAPI dependencies: authentication and per-user rate limiting."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.user import ApiKey, User

# In-memory rate-limit buckets: user_id -> (window_start, request_count)
_rate_buckets: dict[str, tuple[datetime, int]] = {}


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def get_current_user(
    authorization: str = Header(..., alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    raw_key = authorization[7:]
    key_hash = _hash_key(raw_key)
    api_key = (
        await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    ).scalar_one_or_none()
    if api_key is None or not api_key.is_valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    user = await db.get(User, api_key.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user inactive")
    return user


async def rate_limited_user(user: User = Depends(get_current_user)) -> User:
    """Resolve and authenticate user, then enforce per-minute rate limit."""
    uid = str(user.id)
    limit = settings.ratelimit.for_tier(user.tier)
    now = datetime.now(UTC)
    window_start, count = _rate_buckets.get(uid, (now, 0))
    if (now - window_start).total_seconds() >= 60:
        _rate_buckets[uid] = (now, 1)
    else:
        if count >= limit:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        _rate_buckets[uid] = (window_start, count + 1)
    return user
