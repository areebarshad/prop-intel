"""API key management: create, list, revoke."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import ApiKey, User
from app.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut

router = APIRouter(prefix="/keys", tags=["keys"])


def _generate_key() -> tuple[str, str, str]:
    """Return (raw_key, key_hash, key_prefix)."""
    raw = settings.security.api_key_prefix + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12]
    return raw, key_hash, prefix


@router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_key(
    body: ApiKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    raw, key_hash, prefix = _generate_key()
    key = ApiKey(
        user_id=current_user.id,
        name=body.name,
        key_hash=key_hash,
        key_prefix=prefix,
        tier=current_user.tier,
        rate_limit_per_minute=settings.ratelimit.for_tier(current_user.tier),
    )
    db.add(key)
    await db.flush()
    return ApiKeyCreated(
        id=key.id,
        name=key.name,
        key_prefix=key.key_prefix,
        tier=key.tier,
        created_at=key.created_at,
        key=raw,
    )


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKey]:
    return list(
        (
            await db.execute(
                select(ApiKey).where(
                    ApiKey.user_id == current_user.id,
                    ApiKey.revoked_at.is_(None),
                )
            )
        ).scalars()
    )


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    key = await db.get(ApiKey, key_id)
    if key is None or key.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="key not found")
    if key.revoked_at is not None:
        raise HTTPException(status_code=409, detail="key already revoked")
    key.revoked_at = datetime.now(UTC)
