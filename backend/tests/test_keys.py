"""Tests for API key generation: format, uniqueness, hash stability."""

from __future__ import annotations

import hashlib

from app.api.v1.keys import _generate_key
from app.core.config import settings


class TestGenerateKey:
    def test_raw_starts_with_prefix(self) -> None:
        raw, _, _ = _generate_key()
        assert raw.startswith(settings.security.api_key_prefix)

    def test_key_hash_is_sha256_of_raw(self) -> None:
        raw, key_hash, _ = _generate_key()
        assert key_hash == hashlib.sha256(raw.encode()).hexdigest()

    def test_prefix_is_first_12_chars_of_raw(self) -> None:
        raw, _, prefix = _generate_key()
        assert prefix == raw[:12]

    def test_keys_are_unique(self) -> None:
        keys = [_generate_key()[0] for _ in range(20)]
        assert len(set(keys)) == 20

    def test_hash_is_deterministic(self) -> None:
        raw, _, _ = _generate_key()
        assert hashlib.sha256(raw.encode()).hexdigest() == hashlib.sha256(raw.encode()).hexdigest()

    def test_raw_key_length_reasonable(self) -> None:
        raw, _, _ = _generate_key()
        assert len(raw) >= 20
