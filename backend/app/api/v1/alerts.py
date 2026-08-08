"""Alert CRUD endpoints.

Auth note: Phase 3 ships without JWT auth. `user_id` is accepted as a body
field on create and as a query param on list/delete. Phase 4 will replace this
with a real session token.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.alert import Alert
from app.schemas import AlertCreate, AlertOut

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    user_id: UUID = Query(..., description="User whose alerts to list"),
    db: AsyncSession = Depends(get_db),
) -> list[Alert]:
    return list(
        (
            await db.execute(select(Alert).where(Alert.user_id == user_id))
        ).scalars()
    )


@router.post("", response_model=AlertOut, status_code=201)
async def create_alert(body: AlertCreate, db: AsyncSession = Depends(get_db)) -> Alert:
    alert = Alert(
        user_id=body.user_id,
        name=body.name,
        filters=body.filters,
        channel=body.channel,
        channel_target=body.channel_target,
        is_active=True,
    )
    db.add(alert)
    await db.flush()
    return alert


@router.patch("/{alert_id}/toggle", response_model=AlertOut)
async def toggle_alert(alert_id: UUID, db: AsyncSession = Depends(get_db)) -> Alert:
    alert = await db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.is_active = not alert.is_active
    return alert


@router.delete("/{alert_id}", status_code=204)
async def delete_alert(alert_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    alert = await db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    await db.delete(alert)
