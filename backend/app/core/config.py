"""Application settings.

Every value is overridable by environment variable using the ``PROPINTEL_``
prefix, with ``__`` separating nested fields — e.g. ``PROPINTEL_LLM__FAST_MODEL``
selects the extraction model. This is what lets model IDs and API keys be
rotated without touching code, per the platform's configuration contract.
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Circuit breaker: timestamp until which LLM calls are suppressed (0 = closed).
_llm_circuit_open_until: float = 0.0
_LLM_CIRCUIT_DURATION: float = 300.0  # 5 minutes


def open_llm_circuit() -> None:
    """Call after exhausting LLM retries to suppress calls for 5 minutes."""
    global _llm_circuit_open_until
    _llm_circuit_open_until = time.monotonic() + _LLM_CIRCUIT_DURATION


def llm_circuit_is_open() -> bool:
    return time.monotonic() < _llm_circuit_open_until


Environment = Literal["development", "staging", "production", "test"]


class LLMSettings(BaseSettings):
    """Two-tier local LLM routing via Ollama (OpenAI-compatible API).

    ``smart_model`` handles RAG answers, digest narrative, and any summarization
    where a wrong-but-fluent answer would be costly. ``fast_model`` handles the
    high-volume passes — per-document structured extraction and entity-resolution
    adjudication — where cost scales with corpus size.

    Call sites name a tier (``llm.smart()`` / ``llm.fast()``), never a model
    string, so swapping a model is a one-line config change.
    """

    base_url: str = "http://localhost:11434/v1"
    api_key: SecretStr | None = SecretStr("ollama")
    smart_model: str = "qwen2.5:14b"
    fast_model: str = "qwen2.5:14b"

    max_tokens: int = 4096
    # Extraction runs over whole documents; cap the text we send so a 200-page
    # zoning packet can't blow up a single request's cost.
    max_input_chars: int = 60_000
    # Skip the model entirely below this much text — 404 pages and empty JS
    # shells shouldn't cost anything.
    min_input_chars: int = 300
    timeout_seconds: float = 120.0
    max_retries: int = 3
    enabled: bool = True

    model_config = SettingsConfigDict(extra="ignore")

    @property
    def is_configured(self) -> bool:
        if not self.enabled:
            return False
        if not self.base_url or self.api_key is None:
            return False
        if llm_circuit_is_open():
            return False
        return True


class EmbeddingSettings(BaseSettings):
    """fastembed (ONNX Runtime) embeddings — same model weights as sentence-transformers,
    ~10x lower RAM because there is no PyTorch initialisation overhead."""

    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Must match the VECTOR(n) column width in the migrations. Changing the model
    # without changing this (and re-embedding) yields silently wrong search.
    dimensions: int = 384
    batch_size: int = 64
    # Chunk targets, in tokens. Chunks never span a page boundary so that every
    # retrieved passage can cite a page number.
    chunk_tokens: int = 450
    chunk_overlap_tokens: int = 50

    model_config = SettingsConfigDict(extra="ignore")


class SecuritySettings(BaseSettings):
    secret_key: SecretStr = SecretStr("insecure-dev-key-change-me")
    algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12
    api_key_prefix: str = "pk_"

    model_config = SettingsConfigDict(extra="ignore")


class RateLimitSettings(BaseSettings):
    """Per-tier request budgets, applied by dependency on the API routers."""

    free_per_minute: int = 20
    pro_per_minute: int = 120
    enterprise_per_minute: int = 1_000
    anonymous_per_minute: int = 10

    model_config = SettingsConfigDict(extra="ignore")

    def for_tier(self, tier: str) -> int:
        return {
            "free": self.free_per_minute,
            "pro": self.pro_per_minute,
            "enterprise": self.enterprise_per_minute,
        }.get(tier, self.anonymous_per_minute)


class NotifySettings(BaseSettings):
    resend_api_key: SecretStr | None = None
    slack_webhook_url: SecretStr | None = None
    from_email: str = "alerts@propintel.local"

    model_config = SettingsConfigDict(extra="ignore")


class Settings(BaseSettings):
    project_name: str = "PropIntel"
    api_v1_prefix: str = "/api/v1"
    environment: Environment = "development"
    log_level: str = "INFO"
    sentry_dsn: str | None = None

    database_url: str = "postgresql+asyncpg://propintel:propintel@localhost:5433/propintel"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    # Optional Redis URL for shared rate-limit buckets (multi-process deployments).
    # Format: redis://[:password@]host[:port][/db]
    # When absent, deps.py falls back to in-process memory buckets.
    redis_url: str | None = None

    llm: LLMSettings = Field(default_factory=LLMSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    ratelimit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    notify: NotifySettings = Field(default_factory=NotifySettings)

    model_config = SettingsConfigDict(
        env_prefix="PROPINTEL_",
        env_nested_delimiter="__",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, v: str) -> str:
        # A bare postgresql:// URL fails deep inside the async engine with a
        # confusing error; catch it here where the fix is obvious.
        if not v.startswith("postgresql+"):
            raise ValueError(
                "database_url needs an explicit driver, e.g. "
                "postgresql+asyncpg://... (async) or postgresql+psycopg://... (Alembic)"
            )
        return v

    @property
    def sync_database_url(self) -> str:
        """Alembic and one-off scripts run synchronously."""
        return self.database_url.replace("+asyncpg", "+psycopg")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
