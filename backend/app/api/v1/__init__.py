"""API v1 router — aggregates all endpoint modules."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.firms import router as firms_router
from app.api.v1.search import router as search_router
from app.api.v1.signals import router as signals_router

router = APIRouter(prefix="/api/v1")
router.include_router(firms_router)
router.include_router(signals_router)
router.include_router(search_router)
