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
import logging
import time
from datetime import UTC, datetime

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.user import ApiKey, User

log = logging.getLogger(__name__)

# In-memory fallback: user_id -> (window_start_ts, request_count)
_rate_buckets: dict[str, tuple[float, int]] = {}

# Throttle last_used_at writes: key_hash -> last update monotonic timestamp.
_last_used_cache: dict[str, float] = {}
_LAST_USED_TTL: float = 300.0  # 5 minutes

# Redis client — lazily initialised when redis_url is set.
_redis_client: object | None = None
_redis_init_done = False


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _get_redis() -> object | None:
    global _redis_client, _redis_init_done
    if _redis_init_done:
        return _redis_client
    _redis_init_done = True
    if not settings.redis_url:
        return None
    try:
        import redis.asyncio as aioredis

        _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception as exc:
        log.warning("redis init failed, falling back to in-memory rate limiting: %s", exc)
        _redis_client = None
    return _redis_client


_LUA_RATE_LIMIT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local raw = redis.call('GET', key)
if raw then
    local sep = string.find(raw, ':', 1, true)
    local wstart = tonumber(string.sub(raw, 1, sep - 1))
    local count  = tonumber(string.sub(raw, sep + 1))
    if now - wstart < window then
        if count >= limit then return 0 end
        redis.call('SETEX', key, ttl, wstart .. ':' .. (count + 1))
        return 1
    end
end
redis.call('SETEX', key, ttl, now .. ':1')
return 1
"""


async def _check_rate_limit(uid: str, limit: int) -> None:
    now = datetime.now(UTC).timestamp()
    redis = await _get_redis()

    if redis is not None:
        key = f"rl:{uid}"
        try:
            result = await redis.eval(_LUA_RATE_LIMIT, 1, key, now, 60, limit, 65)  # type: ignore[attr-defined]
            if result == 0:
                raise HTTPException(status_code=429, detail="rate limit exceeded")
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
    now_mono = time.monotonic()
    last = _last_used_cache.get(key_hash, 0.0)
    if now_mono - last >= _LAST_USED_TTL:
        api_key.last_used_at = datetime.now(UTC)
        _last_used_cache[key_hash] = now_mono
    return user


async def _resolve_user_from_jwt(token: str, db: AsyncSession) -> User:
    try:
        payload = jwt.decode(
            token,
            settings.security.secret_key.get_secret_value(),
            algorithms=[settings.security.algorithm],
        )
        user_id: str = payload["sub"]
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired") from e
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from e

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


async def rate_limited_ip(request: Request) -> None:
    """IP-based rate limit for unauthenticated endpoints (login, register)."""
    client_ip = request.client.host if request.client else "unknown"
    key = f"ip:{_hash_key(client_ip)}"
    await _check_rate_limit(key, settings.ratelimit.anonymous_per_minute)
