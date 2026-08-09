"""Tests for trend detection logic (Phase 2, Step 4).

Covers the pure-logic helpers (asset-class mix, dedupe-key stability) without
requiring a live database. Integration tests require a DB and skip otherwise.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.trend_detection import (
    PIVOT_BASELINE_THRESHOLD,
    PIVOT_NEW_SHARE_THRESHOLD,
    _derive_asset_class_mix,
    _window_dedupe_key,
)


def _row(permit_type: str, cnt: int) -> MagicMock:
    r = MagicMock()
    r.permit_type = permit_type
    r.cnt = cnt
    r.val = 0
    return r


def test_asset_class_mix_data_center() -> None:
    rows = [_row("Data Center Site Plan", 3), _row("Residential", 1)]
    mix = _derive_asset_class_mix(rows)
    assert "data_center" in mix
    assert mix["data_center"] == pytest.approx(0.75)


def test_asset_class_mix_empty() -> None:
    assert _derive_asset_class_mix([]) == {}


def test_asset_class_mix_unknown_type() -> None:
    rows = [_row("Unknown permit type", 5)]
    mix = _derive_asset_class_mix(rows)
    assert mix == {}


def test_asset_class_mix_sums_to_one() -> None:
    rows = [_row("multifamily", 2), _row("industrial", 3), _row("office", 1)]
    mix = _derive_asset_class_mix(rows)
    assert sum(mix.values()) == pytest.approx(1.0, abs=0.01)


def test_dedupe_key_stable() -> None:
    from datetime import UTC, datetime

    dt = datetime(2026, 1, 1, tzinfo=UTC)
    firm_id = "abc-123"
    key1 = _window_dedupe_key("HIRING_SURGE", firm_id, dt)
    key2 = _window_dedupe_key("HIRING_SURGE", firm_id, dt)
    assert key1 == key2
    assert len(key1) == 40


def test_dedupe_key_differs_by_type() -> None:
    from datetime import UTC, datetime

    dt = datetime(2026, 1, 1, tzinfo=UTC)
    firm_id = "abc-123"
    k1 = _window_dedupe_key("HIRING_SURGE", firm_id, dt)
    k2 = _window_dedupe_key("ASSET_CLASS_PIVOT", firm_id, dt)
    assert k1 != k2


def test_pivot_threshold_constants_are_sane() -> None:
    assert PIVOT_NEW_SHARE_THRESHOLD > PIVOT_BASELINE_THRESHOLD
