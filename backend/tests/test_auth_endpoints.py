"""Integration tests for POST /api/v1/auth/register and /auth/login endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from tests.conftest import _make_user

from app.api.v1.auth import _hash_password

pytestmark = pytest.mark.asyncio


def _no_result() -> MagicMock:
    """scalar_one_or_none() returns None (no existing user)."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    return result


def _existing_result(user: object) -> MagicMock:
    """scalar_one_or_none() returns an existing user."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    return result


class TestRegister:
    async def test_register_returns_201_and_token(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        db_mock.execute.return_value = _no_result()

        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "new@example.com", "password": "securepw1"},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert isinstance(body["access_token"], str) and len(body["access_token"]) > 20
        assert "user_id" in body

    async def test_register_response_schema(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        db_mock.execute.return_value = _no_result()

        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "schema@example.com",
                "password": "securepw2",
                "full_name": "Alice",
                "company": "Acme",
            },
        )

        assert resp.status_code == 201
        body = resp.json()
        assert set(body.keys()) >= {"access_token", "token_type", "user_id"}

    async def test_duplicate_email_via_preflight_check_returns_409(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        existing = _make_user(email="dup@example.com")
        db_mock.execute.return_value = _existing_result(existing)

        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "dup@example.com", "password": "securepw3"},
        )

        assert resp.status_code == 409
        assert "email" in resp.json()["detail"].lower()

    async def test_duplicate_email_via_integrity_error_returns_409(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        db_mock.execute.return_value = _no_result()
        db_mock.flush.side_effect = IntegrityError("", {}, Exception())

        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "race@example.com", "password": "securepw4"},
        )

        assert resp.status_code == 409

    async def test_password_over_72_bytes_is_handled_gracefully(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        """bcrypt silently truncates at 72 bytes; prehash (SHA-256) removes this risk."""
        db_mock.execute.return_value = _no_result()
        long_password = "x" * 100  # 100 bytes > bcrypt's 72-byte limit; schema allows up to 128

        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "longpw@example.com", "password": long_password},
        )

        assert resp.status_code == 201

    async def test_password_too_short_returns_422(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        db_mock.execute.return_value = _no_result()

        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "short@example.com", "password": "tiny"},
        )

        assert resp.status_code == 422

    async def test_invalid_email_returns_422(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": "securepw5"},
        )
        assert resp.status_code == 422


class TestLogin:
    async def test_login_success_returns_200_and_token(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        password = "correct-horse"
        user = _make_user(
            email="login@example.com",
            hashed_password=_hash_password(password),
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        db_mock.execute.return_value = result

        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": password},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert isinstance(body["access_token"], str)
        assert body["user_id"] == str(user.id)

    async def test_login_wrong_password_returns_401(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        user = _make_user(
            email="login2@example.com",
            hashed_password=_hash_password("right-password"),
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        db_mock.execute.return_value = result

        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login2@example.com", "password": "wrong-password"},
        )

        assert resp.status_code == 401

    async def test_login_nonexistent_user_returns_401(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        db_mock.execute.return_value = _no_result()

        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "doesnotmatter"},
        )

        assert resp.status_code == 401

    async def test_login_inactive_user_returns_403(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        user = _make_user(
            email="inactive@example.com",
            hashed_password=_hash_password("pw123456"),
            is_active=False,
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        db_mock.execute.return_value = result

        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "inactive@example.com", "password": "pw123456"},
        )

        assert resp.status_code == 403
