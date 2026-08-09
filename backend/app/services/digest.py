"""Weekly digest generation.

Queries the past N days of material signals, groups them by firm, and asks
Claude to write a 3-5 sentence narrative per firm in broker register. The
narratives are assembled into a markdown digest and stored in the `digests`
table, idempotent on the period (upsert by period_start/period_end).

Usage:
    stats = await run_digest(session)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.alert import Digest
from app.models.enums import SignalType
from app.models.firm import Firm
from app.models.signal import Signal

log = logging.getLogger(__name__)

DIGEST_SCORE_FLOOR = 0.5
DIGEST_PERIOD_DAYS = 7

# Signal types that represent synthesis and should be featured first.
_LEAD_TYPES = frozenset({
    SignalType.HIRING_SURGE,
    SignalType.ASSET_CLASS_PIVOT,
    SignalType.GEOGRAPHIC_EXPANSION,
    SignalType.PERMIT_VOLUME_ANOMALY,
})

_FIRM_PROMPT_TEMPLATE = """\
You are writing a section of a weekly intelligence digest for Virginia \
commercial real estate professionals. Write 3-5 sentences summarising \
{firm_name}'s activity this week in the register of a senior broker — \
precise, no fluff, specific dates and amounts where available.

Signals this week (ordered by importance):
{signal_list}

Write only the narrative sentences. No headers, no bullet points, no preamble.\
"""


@dataclass(slots=True)
class DigestStats:
    firms_covered: int = 0
    signals_included: int = 0
    llm_calls: int = 0
    errors: int = 0

    def __str__(self) -> str:
        return (
            f"{self.firms_covered} firms, {self.signals_included} signals, "
            f"{self.llm_calls} LLM calls, {self.errors} errors"
        )


def _format_signal(sig: Signal) -> str:
    date_str = sig.occurred_at.strftime("%b %-d") if sig.occurred_at else "recent"
    score_tag = "★" if sig.signal_type in _LEAD_TYPES else "·"
    return f"{score_tag} [{date_str}] {sig.title}"


async def _firm_narrative(firm_name: str, signals: list[Signal]) -> str:
    """Generate a Claude-written paragraph for one firm's weekly activity."""
    # Sort: derived signals first (they are the synthesis), then by score desc.
    sorted_sigs = sorted(
        signals,
        key=lambda s: (s.signal_type not in _LEAD_TYPES, -s.score),
    )
    signal_list = "\n".join(_format_signal(s) for s in sorted_sigs[:20])
    prompt = _FIRM_PROMPT_TEMPLATE.format(firm_name=firm_name, signal_list=signal_list)

    if not settings.llm.is_configured:
        # Fallback: plain signal list when LLM is unavailable.
        lines = [f"- {_format_signal(s)}" for s in sorted_sigs[:10]]
        return "\n".join(lines)

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(
        api_key=settings.llm.api_key.get_secret_value(),  # type: ignore[union-attr]
        timeout=settings.llm.timeout_seconds,
        max_retries=settings.llm.max_retries,
    )
    response = await client.messages.create(
        model=settings.llm.smart_model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text if response.content else signal_list


async def run_digest(
    session: AsyncSession,
    *,
    period_days: int = DIGEST_PERIOD_DAYS,
    min_score: float = DIGEST_SCORE_FLOOR,
    as_of: datetime | None = None,
) -> DigestStats:
    """Build a markdown digest of the past period_days and store it."""
    stats = DigestStats()
    now = as_of or datetime.now(UTC)
    # Snap to ISO week boundaries (Monday–Sunday) so daily runs produce a
    # stable (period_start, period_end) key and the upsert deduplicates them.
    weekday = now.weekday()  # 0=Monday, 6=Sunday
    period_start_dt = (now - timedelta(days=weekday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    period_end_dt = period_start_dt + timedelta(days=period_days)
    period_start_d = period_start_dt.date()
    period_end_d = period_end_dt.date()

    # Load signals from the window, above the score floor.
    sigs: list[Signal] = list(
        (
            await session.execute(
                select(Signal)
                .where(
                    Signal.occurred_at >= period_start_dt,
                    Signal.occurred_at <= period_end_dt,
                    Signal.score >= min_score,
                    Signal.firm_id.is_not(None),
                )
                .order_by(Signal.firm_id, Signal.score.desc())
            )
        ).scalars()
    )

    if not sigs:
        log.info("no signals in digest window")
        return stats

    # Group by firm_id.
    by_firm: dict[Any, list[Signal]] = {}
    for sig in sigs:
        by_firm.setdefault(sig.firm_id, []).append(sig)

    # Load firm names in one query.
    firm_ids = list(by_firm.keys())
    firms = {
        str(f.id): f
        for f in (
            await session.execute(select(Firm).where(Firm.id.in_(firm_ids)))
        ).scalars()
    }

    # Generate narrative per firm.
    sections: list[str] = []
    all_signal_ids: list[str] = []

    for firm_id, firm_sigs in sorted(
        by_firm.items(), key=lambda kv: -max(s.score for s in kv[1])
    ):
        firm = firms.get(str(firm_id))
        if firm is None:
            continue
        try:
            narrative = await _firm_narrative(firm.name, firm_sigs)
            if settings.llm.is_configured:
                stats.llm_calls += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("digest LLM call failed for %s: %s", firm.name, exc)
            narrative = "\n".join(_format_signal(s) for s in firm_sigs[:5])
            stats.errors += 1

        sections.append(f"## {firm.name}\n\n{narrative}")
        all_signal_ids.extend(str(s.id) for s in firm_sigs)
        stats.firms_covered += 1
        stats.signals_included += len(firm_sigs)

    if not sections:
        return stats

    period_label = f"{period_start_d.strftime('%B %-d')}–{period_end_d.strftime('%B %-d, %Y')}"
    markdown = (
        f"# PropIntel Weekly Digest — {period_label}\n\n"
        + "\n\n---\n\n".join(sections)
    )

    # Upsert: one digest per period window.
    existing = (
        await session.execute(
            select(Digest).where(
                Digest.period_start == period_start_d,
                Digest.period_end == period_end_d,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        digest = Digest(
            period_start=period_start_d,
            period_end=period_end_d,
            title=f"PropIntel Weekly — {period_label}",
            markdown=markdown,
            signal_count=stats.signals_included,
            firm_count=stats.firms_covered,
            signal_ids=all_signal_ids,
            generated_at=now,
        )
        session.add(digest)
    else:
        existing.markdown = markdown
        existing.signal_count = stats.signals_included
        existing.firm_count = stats.firms_covered
        existing.signal_ids = all_signal_ids
        existing.generated_at = now

    return stats
