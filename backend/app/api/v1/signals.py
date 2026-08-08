"""Signal feed endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.signal import Signal
from app.schemas import PaginatedSignals

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("", response_model=PaginatedSignals)
async def list_signals(
    firm_id: str | None = Query(None),
    signal_type: str | None = Query(None),
    locality: str | None = Query(None),
    asset_class: str | None = Query(None),
    min_score: float = Query(default=0.0, ge=0.0, le=1.0),
    is_derived: bool | None = Query(None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    base = select(Signal).where(Signal.score >= min_score)

    if firm_id:
        base = base.where(Signal.firm_id == firm_id)
    if signal_type:
        base = base.where(Signal.signal_type == signal_type)
    if locality:
        base = base.where(Signal.locality == locality)
    if asset_class:
        base = base.where(Signal.asset_class == asset_class)
    if is_derived is not None:
        base = base.where(Signal.is_derived.is_(is_derived))

    total = (await db.scalar(select(func.count()).select_from(base.subquery()))) or 0
    items = list(
        (
            await db.execute(
                base.order_by(Signal.occurred_at.desc()).limit(limit).offset(offset)
            )
        ).scalars()
    )

    return {"items": items, "total": total, "limit": limit, "offset": offset}
