"""Pydantic response and request schemas for the PropIntel API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, model_validator


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

    @computed_field
    @property
    def employees_count(self) -> int | None:
        return self.employee_count


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

    @computed_field
    @property
    def body(self) -> str | None:
        return self.summary


class FirmTimeline(BaseModel):
    firm: FirmDetail
    signals: list[SignalOut]
    total: int
    page: int
    page_size: int


class PaginatedFirms(BaseModel):
    items: list[FirmSummary]
    total: int
    page: int
    page_size: int


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

_SLACK_WEBHOOK_PREFIX = "https://hooks.slack.com/"


class AlertCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    filters: dict[str, Any] = Field(default_factory=dict)
    channel: str = Field(default="email", pattern="^(email|slack)$")
    channel_target: str | None = None

    @model_validator(mode="after")
    def _validate_channel_target(self) -> "AlertCreate":
        t = self.channel_target
        if self.channel == "slack" and t is not None:
            if not t.startswith(_SLACK_WEBHOOK_PREFIX):
                raise ValueError(
                    "channel_target for slack must start with https://hooks.slack.com/"
                )
        if self.channel == "email" and t is not None:
            if "@" not in t or t.startswith("@") or t.endswith("@"):
                raise ValueError("channel_target for email must be a valid address")
        return self


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
    created_at: datetime


# ── Auth ───────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str | None
    company: str | None
    tier: str
    is_active: bool
    is_superuser: bool
    last_login_at: datetime | None
    created_at: datetime


# ── API Keys ───────────────────────────────────────────────────────────────────

class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str
    tier: str
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime


class ApiKeyCreated(ApiKeyOut):
    """Returned only at creation — the plaintext key is not stored."""
    key: str
