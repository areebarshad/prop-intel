"""Digest endpoints — fetch generated weekly reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.alert import Digest
from app.schemas import DigestOut

router = APIRouter(prefix="/digests", tags=["digests"])


@router.get("/latest", response_model=DigestOut)
async def latest_digest(db: AsyncSession = Depends(get_db)) -> Digest:
    """Return the most recently generated weekly digest."""
    digest = (
        await db.execute(select(Digest).order_by(Digest.generated_at.desc()).limit(1))
    ).scalar_one_or_none()
    if digest is None:
        raise HTTPException(status_code=404, detail="no digest generated yet")
    return digest


@router.post("/generate", response_model=DigestOut)
async def generate_digest(db: AsyncSession = Depends(get_db)) -> Digest:
    """Generate (or refresh) the weekly digest on demand and return it."""
    from app.services.digest import run_digest

    await run_digest(db)
    await db.flush()

    digest = (
        await db.execute(select(Digest).order_by(Digest.generated_at.desc()).limit(1))
    ).scalar_one_or_none()
    if digest is None:
        raise HTTPException(status_code=500, detail="digest generation produced no output")
    return digest
