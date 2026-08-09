"""Shared fixtures for endpoint integration tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_current_user, rate_limited_ip
from app.db.session import get_db
from app.main import app


def _make_user(**kwargs) -> SimpleNamespace:
    """Lightweight stand-in for a User ORM instance; works with Pydantic from_attributes."""
    defaults = dict(
        id=uuid.uuid4(),
        email="fixture@example.com",
        hashed_password="x",
        tier="free",
        is_active=True,
        is_superuser=False,
        full_name=None,
        company=None,
        last_login_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_api_key(user_id: uuid.UUID, **kwargs) -> SimpleNamespace:
    """Lightweight stand-in for an ApiKey ORM instance."""
    defaults = dict(
        id=uuid.uuid4(),
        user_id=user_id,
        name="test-key",
        key_prefix="pk_testkey",
        tier="free",
        rate_limit_per_minute=20,
        last_used_at=None,
        expires_at=None,
        revoked_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _db_mock() -> AsyncMock:
    """Async session mock that populates SA-model defaults via __dict__ on add()."""

    def _add(obj):
        # Bypass SQLAlchemy attribute instrumentation so we can set server-side
        # defaults that a real flush would populate but a mock flush won't.
        d = obj.__dict__ if hasattr(obj, "__dict__") else {}
        if d.get("id") is None:
            d["id"] = uuid.uuid4()
        if d.get("created_at") is None:
            d["created_at"] = datetime.now(UTC)
        if d.get("updated_at") is None:
            d["updated_at"] = datetime.now(UTC)

    session = AsyncMock()
    session.add = MagicMock(side_effect=_add)
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    return session


@pytest.fixture
def db_mock() -> AsyncMock:
    return _db_mock()


@pytest.fixture
def fixture_user() -> User:
    return _make_user()


@pytest_asyncio.fixture
async def client(db_mock: AsyncMock) -> AsyncIterator[AsyncClient]:
    """AsyncClient with DB and rate-limit deps overridden."""

    async def _get_db():
        yield db_mock

    async def _no_rate_limit():
        return None

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[rate_limited_ip] = _no_rate_limit
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(rate_limited_ip, None)


@pytest_asyncio.fixture
async def authed_client(db_mock: AsyncMock, fixture_user: User) -> AsyncIterator[AsyncClient]:
    """AsyncClient with DB, rate-limit, and auth deps overridden."""

    async def _get_db():
        yield db_mock

    async def _no_rate_limit():
        return None

    async def _get_user():
        return fixture_user

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[rate_limited_ip] = _no_rate_limit
    app.dependency_overrides[get_current_user] = _get_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(rate_limited_ip, None)
    app.dependency_overrides.pop(get_current_user, None)
