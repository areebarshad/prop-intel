"""Tests for alert delivery — _signal_matches logic."""

from __future__ import annotations

import uuid
import pytest

from app.services.alert_delivery import DEFAULT_LOOKBACK_HOURS, DeliveryStats, _signal_matches
from app.models.enums import SignalType


class _FakeSig:
    def __init__(
        self,
        firm_id: uuid.UUID | None = None,
        signal_type: str = SignalType.PERMIT_FILING,
        locality: str | None = None,
        asset_class: str | None = None,
        score: float = 0.7,
    ):
        self.firm_id = firm_id or uuid.uuid4()
        self.signal_type = signal_type
        self.locality = locality
        self.asset_class = asset_class
        self.score = score


def test_empty_filters_matches_everything() -> None:
    sig = _FakeSig()
    assert _signal_matches(sig, {}) is True  # type: ignore[arg-type]


def test_firm_id_filter_match() -> None:
    fid = uuid.uuid4()
    sig = _FakeSig(firm_id=fid)
    assert _signal_matches(sig, {"firm_ids": [str(fid)]}) is True  # type: ignore[arg-type]


def test_firm_id_filter_no_match() -> None:
    sig = _FakeSig(firm_id=uuid.uuid4())
    assert _signal_matches(sig, {"firm_ids": [str(uuid.uuid4())]}) is False  # type: ignore[arg-type]


def test_signal_type_filter_match() -> None:
    sig = _FakeSig(signal_type=SignalType.HIRING_SURGE)
    assert _signal_matches(sig, {"signal_types": [SignalType.HIRING_SURGE]}) is True  # type: ignore[arg-type]


def test_signal_type_filter_no_match() -> None:
    sig = _FakeSig(signal_type=SignalType.PERMIT_FILING)
    assert _signal_matches(sig, {"signal_types": [SignalType.HIRING_SURGE]}) is False  # type: ignore[arg-type]


def test_locality_filter_match() -> None:
    sig = _FakeSig(locality="Fairfax")
    assert _signal_matches(sig, {"localities": ["Fairfax"]}) is True  # type: ignore[arg-type]


def test_locality_filter_no_match() -> None:
    sig = _FakeSig(locality="Arlington")
    assert _signal_matches(sig, {"localities": ["Fairfax"]}) is False  # type: ignore[arg-type]


def test_asset_class_filter_match() -> None:
    sig = _FakeSig(asset_class="office")
    assert _signal_matches(sig, {"asset_classes": ["office"]}) is True  # type: ignore[arg-type]


def test_min_score_filter_pass() -> None:
    sig = _FakeSig(score=0.8)
    assert _signal_matches(sig, {"min_score": 0.7}) is True  # type: ignore[arg-type]


def test_min_score_filter_fail() -> None:
    sig = _FakeSig(score=0.6)
    assert _signal_matches(sig, {"min_score": 0.7}) is False  # type: ignore[arg-type]


def test_combined_filters_all_match() -> None:
    fid = uuid.uuid4()
    sig = _FakeSig(firm_id=fid, signal_type=SignalType.HIRING_SURGE, locality="Fairfax", score=0.9)
    filters = {
        "firm_ids": [str(fid)],
        "signal_types": [SignalType.HIRING_SURGE],
        "localities": ["Fairfax"],
        "min_score": 0.5,
    }
    assert _signal_matches(sig, filters) is True  # type: ignore[arg-type]


def test_combined_filters_one_fails() -> None:
    fid = uuid.uuid4()
    sig = _FakeSig(firm_id=fid, signal_type=SignalType.PERMIT_FILING, locality="Fairfax", score=0.9)
    filters = {
        "firm_ids": [str(fid)],
        "signal_types": [SignalType.HIRING_SURGE],  # mismatch
    }
    assert _signal_matches(sig, filters) is False  # type: ignore[arg-type]


def test_delivery_stats_str() -> None:
    s = DeliveryStats(alerts_checked=5, signals_matched=3, deliveries_sent=2, deliveries_skipped=1)
    text = str(s)
    assert "5 alerts" in text
    assert "3 matches" in text


def test_lookback_hours_constant() -> None:
    assert DEFAULT_LOOKBACK_HOURS == 25
