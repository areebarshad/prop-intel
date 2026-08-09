"""Firm endpoints: list, detail, and timeline."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.firm import Firm
from app.models.signal import Signal
from app.schemas import FirmDetail, FirmTimeline, PaginatedFirms

router = APIRouter(prefix="/firms", tags=["firms"])


@router.get("", response_model=PaginatedFirms)
async def list_firms(
    q: str | None = Query(None, description="Search by name"),
    firm_type: str | None = Query(None),
    asset_class: str | None = Query(None),
    locality: str | None = Query(None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    query = select(Firm).where(Firm.is_active.is_(True))
    if q:
        pattern = f"%{q}%"
        query = query.where(Firm.name.ilike(pattern))
    if firm_type:
        query = query.where(Firm.firm_type == firm_type)
    if asset_class:
        query = query.where(Firm.asset_classes.any(asset_class))  # type: ignore[arg-type]
    if locality:
        query = query.where(Firm.localities.any(locality))  # type: ignore[arg-type]

    total = (await db.scalar(select(func.count()).select_from(query.subquery()))) or 0
    offset = (page - 1) * page_size
    items = list(
        (await db.execute(query.order_by(Firm.name).limit(page_size).offset(offset))).scalars()
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{firm_id}", response_model=FirmDetail)
async def get_firm(firm_id: UUID, db: AsyncSession = Depends(get_db)) -> Firm:
    firm = await db.get(Firm, firm_id)
    if firm is None:
        raise HTTPException(status_code=404, detail="firm not found")
    return firm


@router.get("/{firm_id}/timeline", response_model=FirmTimeline)
async def firm_timeline(
    firm_id: UUID,
    signal_types: list[str] = Query(default_factory=list),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    firm = await db.get(Firm, firm_id)
    if firm is None:
        raise HTTPException(status_code=404, detail="firm not found")

    count_base = select(func.count()).select_from(Signal).where(Signal.firm_id == firm_id)
    if signal_types:
        count_base = count_base.where(Signal.signal_type.in_(signal_types))
    total = (await db.scalar(count_base)) or 0

    from sqlalchemy import nullslast

    signals_query = select(Signal).where(Signal.firm_id == firm_id)
    if signal_types:
        signals_query = signals_query.where(Signal.signal_type.in_(signal_types))
    offset = (page - 1) * page_size
    signals_query = (
        signals_query.order_by(nullslast(Signal.occurred_at.desc())).limit(page_size).offset(offset)
    )
    signals = list((await db.execute(signals_query)).scalars())

    return {
        "firm": firm,
        "signals": signals,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
