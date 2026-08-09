"""Integration tests for POST/GET/DELETE /api/v1/keys endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from tests.conftest import _make_api_key

pytestmark = pytest.mark.asyncio


class TestCreateKey:
    async def test_create_key_returns_201(self, authed_client: AsyncClient, db_mock: AsyncMock):
        resp = await authed_client.post("/api/v1/keys", json={"name": "my-integration-key"})

        assert resp.status_code == 201

    async def test_create_key_response_schema(
        self, authed_client: AsyncClient, db_mock: AsyncMock
    ):
        resp = await authed_client.post("/api/v1/keys", json={"name": "schema-test"})

        assert resp.status_code == 201
        body = resp.json()
        assert set(body.keys()) >= {"id", "name", "key_prefix", "tier", "created_at", "key"}

    async def test_create_key_raw_key_starts_with_prefix(
        self, authed_client: AsyncClient, db_mock: AsyncMock
    ):
        resp = await authed_client.post("/api/v1/keys", json={"name": "prefix-check"})

        assert resp.status_code == 201
        assert resp.json()["key"].startswith("pk_")

    async def test_create_key_prefix_field_is_first_12_chars_of_raw(
        self, authed_client: AsyncClient, db_mock: AsyncMock
    ):
        resp = await authed_client.post("/api/v1/keys", json={"name": "prefix-len"})

        assert resp.status_code == 201
        body = resp.json()
        assert body["key_prefix"] == body["key"][:12]

    async def test_create_key_name_blank_returns_422(
        self, authed_client: AsyncClient, db_mock: AsyncMock
    ):
        resp = await authed_client.post("/api/v1/keys", json={"name": ""})
        assert resp.status_code == 422

    async def test_create_key_no_auth_returns_422(self, client: AsyncClient, db_mock: AsyncMock):
        """Without auth override the header is missing → 422 (missing required header)."""
        resp = await client.post("/api/v1/keys", json={"name": "no-auth"})
        assert resp.status_code in (401, 422)


class TestDeleteKey:
    async def test_revoke_key_returns_204(
        self, authed_client: AsyncClient, db_mock: AsyncMock, fixture_user
    ):
        key = _make_api_key(fixture_user.id)
        db_mock.get.return_value = key

        resp = await authed_client.delete(f"/api/v1/keys/{key.id}")

        assert resp.status_code == 204
        assert resp.content == b""

    async def test_revoke_nonexistent_key_returns_404(
        self, authed_client: AsyncClient, db_mock: AsyncMock
    ):
        db_mock.get.return_value = None

        resp = await authed_client.delete(f"/api/v1/keys/{uuid.uuid4()}")

        assert resp.status_code == 404

    async def test_revoke_other_users_key_returns_404(
        self, authed_client: AsyncClient, db_mock: AsyncMock
    ):
        other_user_id = uuid.uuid4()
        key = _make_api_key(other_user_id)
        db_mock.get.return_value = key

        resp = await authed_client.delete(f"/api/v1/keys/{key.id}")

        assert resp.status_code == 404

    async def test_revoke_already_revoked_key_returns_409(
        self, authed_client: AsyncClient, db_mock: AsyncMock, fixture_user
    ):
        key = _make_api_key(fixture_user.id, revoked_at=datetime.now(UTC))
        db_mock.get.return_value = key

        resp = await authed_client.delete(f"/api/v1/keys/{key.id}")

        assert resp.status_code == 409


class TestListKeys:
    async def test_list_keys_returns_200_and_list(
        self, authed_client: AsyncClient, db_mock: AsyncMock, fixture_user
    ):
        key1 = _make_api_key(fixture_user.id, name="k1")
        key2 = _make_api_key(fixture_user.id, name="k2")
        result = MagicMock()
        result.scalars.return_value = iter([key1, key2])
        db_mock.execute.return_value = result

        resp = await authed_client.get("/api/v1/keys")

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) == 2

    async def test_list_keys_empty(
        self, authed_client: AsyncClient, db_mock: AsyncMock
    ):
        result = MagicMock()
        result.scalars.return_value = iter([])
        db_mock.execute.return_value = result

        resp = await authed_client.get("/api/v1/keys")

        assert resp.status_code == 200
        assert resp.json() == []
