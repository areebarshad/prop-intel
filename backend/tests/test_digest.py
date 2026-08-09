"""Tests for digest service logic — no DB or LLM."""

from __future__ import annotations

from app.models.enums import SignalType
from app.services.digest import (
    DIGEST_PERIOD_DAYS,
    DIGEST_SCORE_FLOOR,
    DigestStats,
    _format_signal,
)


class _FakeSig:
    def __init__(
        self, signal_type: SignalType, title: str, score: float, occurred_at: object = None
    ) -> None:
        self.signal_type = signal_type
        self.title = title
        self.score = score
        self.occurred_at = occurred_at


def test_digest_stats_str() -> None:
    s = DigestStats(firms_covered=3, signals_included=12, llm_calls=3, errors=0)
    text = str(s)
    assert "3 firms" in text
    assert "12 signals" in text


def test_digest_stats_defaults() -> None:
    s = DigestStats()
    assert s.firms_covered == 0
    assert s.errors == 0


def test_constants() -> None:
    assert DIGEST_PERIOD_DAYS == 7
    assert DIGEST_SCORE_FLOOR == 0.5


def test_format_signal_lead_type_gets_star() -> None:
    sig = _FakeSig(SignalType.HIRING_SURGE, "Big hire", 0.9)
    text = _format_signal(sig)  # type: ignore[arg-type]
    assert text.startswith("★")
    assert "Big hire" in text


def test_format_signal_plain_type_gets_dot() -> None:
    sig = _FakeSig(SignalType.PERMIT_FILING, "Filed permit", 0.6)
    text = _format_signal(sig)  # type: ignore[arg-type]
    assert text.startswith("·")


def test_format_signal_no_date_shows_recent() -> None:
    sig = _FakeSig(SignalType.PERMIT_FILING, "Permit", 0.5, occurred_at=None)
    text = _format_signal(sig)  # type: ignore[arg-type]
    assert "recent" in text
