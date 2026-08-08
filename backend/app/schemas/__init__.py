"""Pydantic response and request schemas for the PropIntel API."""

from __future__ import annotations

from datetime import date, datetime
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


# ── RAG ────────────────────────────────────────────────────────────────────────

class AskQuery(BaseModel):
    q: str = Field(min_length=1, max_length=1000)
    firm_id: UUID | None = None
    top_k: int = Field(default=8, ge=1, le=20)
    min_similarity: float = Field(default=0.35, ge=0.0, le=1.0)


class Citation(BaseModel):
    url: str | None
    page_number: int | None
    excerpt: str


class AskAnswer(BaseModel):
    answer: str
    citations: list[Citation]
    query: str
    passages_found: int


# ── Digest ─────────────────────────────────────────────────────────────────────

class DigestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    period_start: date
    period_end: date
    title: str | None
    markdown: str
    signal_count: int
    firm_count: int
    signal_ids: list[str]
    generated_at: datetime | None


# ── Alerts ─────────────────────────────────────────────────────────────────────

class AlertCreate(BaseModel):
    user_id: UUID
    name: str = Field(min_length=1, max_length=200)
    filters: dict[str, Any] = Field(default_factory=dict)
    channel: str = Field(default="email", pattern="^(email|slack)$")
    channel_target: str | None = None


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str
    filters: dict[str, Any]
    channel: str
    channel_target: str | None
    is_active: bool
    last_fired_at: datetime | None
    fire_count: int
