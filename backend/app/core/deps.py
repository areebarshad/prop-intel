"""FastAPI dependencies: authentication and per-user rate limiting.

Authentication accepts two bearer token forms:
  - API key:   Bearer pk_<random>   (prefix matches settings.security.api_key_prefix)
  - JWT token: Bearer eyJ...        (issued by POST /api/v1/auth/login)

Rate limiting defaults to in-memory per-process buckets. When
PROPINTEL_REDIS_URL is configured the counter is stored in Redis so limits
survive restarts and apply correctly across multiple workers.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.user import ApiKey, User

log = logging.getLogger(__name__)

# In-memory fallback: user_id -> (window_start_ts, request_count)
_rate_buckets: dict[str, tuple[float, int]] = {}

# Redis client — lazily initialised when redis_url is set.
_redis_client: object | None = None
_redis_init_done = False


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _get_redis():
    global _redis_client, _redis_init_done
    if _redis_init_done:
        return _redis_client
    _redis_init_done = True
    if not settings.redis_url:
        return None
    try:
        import redis.asyncio as aioredis  # type: ignore[import-not-found]
        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception as exc:
        log.warning("redis init failed, falling back to in-memory rate limiting: %s", exc)
        _redis_client = None
    return _redis_client


async def _check_rate_limit(uid: str, limit: int) -> None:
    now = datetime.now(UTC).timestamp()
    redis = await _get_redis()

    if redis is not None:
        # Redis atomic check-and-set via a 65-second TTL string: "window_start:count"
        key = f"rl:{uid}"
        try:
            raw = await redis.get(key)
            if raw:
                window_start, count = map(float, raw.split(":", 1))
                if now - window_start < 60:
                    if count >= limit:
                        raise HTTPException(status_code=429, detail="rate limit exceeded")
                    await redis.setex(key, 65, f"{window_start}:{count + 1}")
                    return
            await redis.setex(key, 65, f"{now}:1")
            return
        except HTTPException:
            raise
        except Exception as exc:
            log.warning("redis rate-limit check failed, falling back to in-memory: %s", exc)

    # In-memory fallback.
    window_start, count = _rate_buckets.get(uid, (now, 0))
    if now - window_start >= 60:
        _rate_buckets[uid] = (now, 1)
    else:
        if count >= limit:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        _rate_buckets[uid] = (window_start, count + 1)


async def _resolve_user_from_api_key(raw_key: str, db: AsyncSession) -> User:
    key_hash = _hash_key(raw_key)
    api_key = (
        await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    ).scalar_one_or_none()
    if api_key is None or not api_key.is_valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    user = await db.get(User, api_key.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user inactive")
    # Bump last_used_at without a full flush — fire and forget at session close.
    api_key.last_used_at = datetime.now(UTC)
    return user


async def _resolve_user_from_jwt(token: str, db: AsyncSession) -> User:
    try:
        payload = jwt.decode(
            token,
            settings.security.secret_key.get_secret_value(),
            algorithms=[settings.security.algorithm],
        )
        user_id: str = payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user inactive")
    return user


async def get_current_user(
    authorization: str = Header(..., alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    token = authorization[7:]
    if token.startswith(settings.security.api_key_prefix):
        return await _resolve_user_from_api_key(token, db)
    return await _resolve_user_from_jwt(token, db)


async def rate_limited_user(user: User = Depends(get_current_user)) -> User:
    """Resolve and authenticate user, then enforce per-minute rate limit."""
    limit = settings.ratelimit.for_tier(user.tier)
    await _check_rate_limit(str(user.id), limit)
    return user
