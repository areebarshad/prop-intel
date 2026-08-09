"""Tests for JWT auth: token issuance, JWT decode, password hashing."""

from __future__ import annotations

import jwt

from app.core.config import settings
from app.api.v1.auth import _hash_password, _verify_password, _issue_token


class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        pw = "hunter2!"
        h = _hash_password(pw)
        assert h != pw

    def test_verify_correct(self):
        pw = "correct-horse-battery"
        assert _verify_password(pw, _hash_password(pw))

    def test_verify_wrong(self):
        assert not _verify_password("wrong", _hash_password("right"))

    def test_different_hashes_for_same_password(self):
        pw = "samepass"
        assert _hash_password(pw) != _hash_password(pw)


class TestJWTIssuance:
    def test_token_is_string(self):
        tok = _issue_token("user-123")
        assert isinstance(tok, str)

    def test_token_decodes_correctly(self):
        tok = _issue_token("user-abc")
        payload = jwt.decode(
            tok,
            settings.security.secret_key.get_secret_value(),
            algorithms=[settings.security.algorithm],
        )
        assert payload["sub"] == "user-abc"

    def test_token_has_exp(self):
        tok = _issue_token("user-abc")
        payload = jwt.decode(
            tok,
            settings.security.secret_key.get_secret_value(),
            algorithms=[settings.security.algorithm],
        )
        assert "exp" in payload
        assert payload["exp"] > payload["iat"]

    def test_token_expiry_matches_ttl(self):
        tok = _issue_token("u")
        payload = jwt.decode(
            tok,
            settings.security.secret_key.get_secret_value(),
            algorithms=[settings.security.algorithm],
        )
        expected_ttl = settings.security.access_token_ttl_minutes * 60
        actual_ttl = payload["exp"] - payload["iat"]
        assert abs(actual_ttl - expected_ttl) < 2


class TestSecuritySettings:
    def test_algorithm_is_hs256(self):
        assert settings.security.algorithm == "HS256"

    def test_api_key_prefix(self):
        assert settings.security.api_key_prefix == "pk_"

    def test_ttl_positive(self):
        assert settings.security.access_token_ttl_minutes > 0
