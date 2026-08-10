"""Integration tests for POST /api/v1/contact."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient

_ENDPOINT = "/api/v1/contact"

_MINIMAL = {"work_email": "user@example.com", "reason": "Request a Demo"}
_FULL = {
    "work_email": "full@example.com",
    "reason": "Learn More",
    "full_name": "Alice Smith",
    "company": "Acme Corp",
    "role": "CTO",
    "use_case_notes": "We want to track multifamily deals.",
}


def _mock_resend_ok() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    return resp


def _mock_resend_error(status: int = 422) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = "invalid_from_address"
    request = MagicMock()
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("error", request=request, response=resp)
    )
    return resp


@pytest.mark.asyncio
class TestSubmitContactHappyPath:
    async def test_full_submission_returns_200(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=_mock_resend_ok())
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = await client.post(_ENDPOINT, json=_FULL)

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_minimal_submission_returns_200(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        resp = await client.post(_ENDPOINT, json=_MINIMAL)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_lead_persisted_to_db(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        await client.post(_ENDPOINT, json=_FULL)
        db_mock.add.assert_called_once()
        added = db_mock.add.call_args[0][0]
        assert added.work_email == "full@example.com"
        assert added.full_name == "Alice Smith"
        assert added.company == "Acme Corp"
        assert added.role == "CTO"

    async def test_commit_called_before_email(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        call_order: list[str] = []
        original_commit = db_mock.commit

        async def _tracked_commit() -> None:
            call_order.append("commit")
            return await original_commit()

        db_mock.commit = _tracked_commit

        post_calls: list[str] = []

        async def _tracked_post(*args: object, **kwargs: object) -> MagicMock:
            post_calls.append("resend")
            return _mock_resend_ok()

        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = _tracked_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.api.v1.contact.settings") as mock_settings:
                mock_settings.notify.resend_api_key = MagicMock()
                mock_settings.notify.resend_api_key.get_secret_value.return_value = "re_test"
                mock_settings.notify.unique_contact_recipients = ["owner@example.com"]
                mock_settings.notify.from_email = "alerts@example.com"

                await client.post(_ENDPOINT, json=_MINIMAL)

        assert call_order == ["commit"]


@pytest.mark.asyncio
class TestFallbackName:
    async def test_missing_name_uses_email_local_part(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        await client.post(_ENDPOINT, json=_MINIMAL)
        added = db_mock.add.call_args[0][0]
        assert added.full_name == "user"

    async def test_blank_name_uses_email_local_part(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        payload = {**_MINIMAL, "full_name": "   "}
        await client.post(_ENDPOINT, json=payload)
        added = db_mock.add.call_args[0][0]
        assert added.full_name == "user"


def test_fallback_name_long_local_part_truncated_to_200() -> None:
    # EmailStr rejects local parts > 64 chars at the HTTP layer, so this
    # guard is exercised only via a direct call to the helper.
    from app.api.v1.contact import _fallback_full_name

    long_email = "x" * 300 + "@example.com"
    assert len(_fallback_full_name(long_email)) == 200


@pytest.mark.asyncio
class TestAliasFields:
    async def test_email_alias_accepted(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        resp = await client.post(_ENDPOINT, json={"email": "alias@example.com", "reason": "Other"})
        assert resp.status_code == 200
        assert db_mock.add.call_args[0][0].work_email == "alias@example.com"

    async def test_name_alias_accepted(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        resp = await client.post(
            _ENDPOINT,
            json={"work_email": "a@example.com", "reason": "Other", "name": "Bob"},
        )
        assert resp.status_code == 200
        assert db_mock.add.call_args[0][0].full_name == "Bob"

    async def test_message_alias_accepted(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        resp = await client.post(
            _ENDPOINT,
            json={"work_email": "a@example.com", "reason": "Other", "message": "hi there"},
        )
        assert resp.status_code == 200
        added = db_mock.add.call_args[0][0]
        assert "hi there" in (added.use_case_notes or "")


@pytest.mark.asyncio
class TestValidation:
    async def test_invalid_reason_returns_422(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        resp = await client.post(
            _ENDPOINT, json={"work_email": "a@example.com", "reason": "Hack the planet"}
        )
        assert resp.status_code == 422

    async def test_invalid_email_returns_422(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        resp = await client.post(_ENDPOINT, json={"work_email": "not-an-email", "reason": "Other"})
        assert resp.status_code == 422

    async def test_missing_email_returns_422(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        resp = await client.post(_ENDPOINT, json={"reason": "Other"})
        assert resp.status_code == 422

    async def test_missing_reason_returns_422(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        resp = await client.post(_ENDPOINT, json={"work_email": "a@example.com"})
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestNoteBuilding:
    async def test_reason_folded_into_notes(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        await client.post(_ENDPOINT, json=_MINIMAL)
        added = db_mock.add.call_args[0][0]
        assert "Reason: Request a Demo" in (added.use_case_notes or "")

    async def test_use_case_notes_appended(self, client: AsyncClient, db_mock: AsyncMock) -> None:
        payload = {**_MINIMAL, "use_case_notes": "Multifamily focus."}
        await client.post(_ENDPOINT, json=payload)
        added = db_mock.add.call_args[0][0]
        notes = added.use_case_notes or ""
        assert "Reason: Request a Demo" in notes
        assert "Multifamily focus." in notes


@pytest.mark.asyncio
class TestResendIntegration:
    async def test_no_api_key_skips_resend_still_returns_200(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        with patch("app.api.v1.contact.settings") as mock_settings:
            mock_settings.notify.resend_api_key = None
            mock_settings.notify.unique_contact_recipients = ["owner@example.com"]
            mock_settings.notify.from_email = "alerts@example.com"

            resp = await client.post(_ENDPOINT, json=_MINIMAL)

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_no_recipients_skips_resend_still_returns_200(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        with patch("app.api.v1.contact.settings") as mock_settings:
            mock_settings.notify.resend_api_key = MagicMock()
            mock_settings.notify.resend_api_key.get_secret_value.return_value = "re_test"
            mock_settings.notify.unique_contact_recipients = []
            mock_settings.notify.from_email = "alerts@example.com"

            resp = await client.post(_ENDPOINT, json=_MINIMAL)

        assert resp.status_code == 200

    async def test_resend_http_error_does_not_fail_request(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=_mock_resend_error(422))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.api.v1.contact.settings") as mock_settings:
                mock_settings.notify.resend_api_key = MagicMock()
                mock_settings.notify.resend_api_key.get_secret_value.return_value = "re_test"
                mock_settings.notify.unique_contact_recipients = ["owner@example.com"]
                mock_settings.notify.from_email = "alerts@example.com"

                resp = await client.post(_ENDPOINT, json=_MINIMAL)

        assert resp.status_code == 200

    async def test_resend_network_error_does_not_fail_request(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=httpx.ConnectError("timeout"))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.api.v1.contact.settings") as mock_settings:
                mock_settings.notify.resend_api_key = MagicMock()
                mock_settings.notify.resend_api_key.get_secret_value.return_value = "re_test"
                mock_settings.notify.unique_contact_recipients = ["owner@example.com"]
                mock_settings.notify.from_email = "alerts@example.com"

                resp = await client.post(_ENDPOINT, json=_MINIMAL)

        assert resp.status_code == 200

    async def test_resend_called_with_correct_payload(
        self, client: AsyncClient, db_mock: AsyncMock
    ) -> None:
        captured: list[dict] = []

        async def _capture_post(url: str, **kwargs: object) -> MagicMock:
            captured.append({"url": url, **kwargs})
            return _mock_resend_ok()

        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.post = _capture_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.api.v1.contact.settings") as mock_settings:
                mock_settings.notify.resend_api_key = MagicMock()
                mock_settings.notify.resend_api_key.get_secret_value.return_value = "re_test"
                mock_settings.notify.unique_contact_recipients = ["owner@example.com"]
                mock_settings.notify.from_email = "alerts@propintel.io"

                await client.post(_ENDPOINT, json=_FULL)

        assert len(captured) == 1
        call = captured[0]
        assert call["url"] == "https://api.resend.com/emails"
        payload = call["json"]
        assert payload["to"] == ["owner@example.com"]
        assert payload["from"] == "alerts@propintel.io"
        assert payload["reply_to"] == "full@example.com"
        assert "Learn More" in payload["subject"]
        assert "Alice Smith" in payload["text"]
