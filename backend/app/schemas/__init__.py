"""Pydantic response and request schemas for the PropIntel API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FirmSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    firm_type: str
    city: str | None
    county: str | None
    state: str
    website: str | None
    asset_classes: list[str]
    localities: list[str]
    description: str | None
    is_active: bool
    last_activity_at: datetime | None


class FirmDetail(FirmSummary):
    canonical_name: str
    hq_address: str | None
    phone: str | None
    focus_areas: list[str]
    employee_count: int | None
    pipeline_project_count: int
    founded_year: int | None
    embedding_updated_at: datetime | None


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    firm_id: UUID | None
    signal_type: str
    title: str
    summary: str | None
    score: float
    locality: str | None
    asset_class: str | None
    occurred_at: datetime | None
    detected_at: datetime | None
    payload: dict[str, Any]
    is_derived: bool


class FirmTimeline(BaseModel):
    firm: FirmDetail
    signals: list[SignalOut]
    total: int


class SearchQuery(BaseModel):
    q: str = Field(min_length=1, max_length=500)
    asset_classes: list[str] = Field(default_factory=list)
    localities: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=50)


class SearchResult(BaseModel):
    firms: list[FirmSummary]
    query: str
    semantic: bool


class PaginatedSignals(BaseModel):
    items: list[SignalOut]
    total: int
    limit: int
    offset: int
