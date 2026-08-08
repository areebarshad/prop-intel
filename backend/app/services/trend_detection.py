"""Trend and anomaly detection over the signal stream.

Computes rolling TrendWindow aggregates and derives four signal types that
can't be emitted from a single document:

  HIRING_SURGE          — open job count jumped 1.5× in the current window
  ASSET_CLASS_PIVOT     — the share of a firm's permits in a new asset class
                          exceeds 30% when it was below 10% in prior windows
  GEOGRAPHIC_EXPANSION  — first permit or project in a new locality
  PERMIT_VOLUME_ANOMALY — current permit count is >2σ above the firm's
                          trailing baseline

All derived signals write to the `signals` table with ``is_derived=True`` and
a ``dedupe_key`` scoped to the window so re-running detection is idempotent.

Typical usage in cli.py::

    from app.services.trend_detection import run_trend_detection
    stats = await run_trend_detection(session)
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AssetClass, SignalType
from app.models.firm import Firm
from app.models.person import JobPosting
from app.models.project import Permit
from app.models.signal import Signal, TrendWindow

log = logging.getLogger(__name__)

# Thresholds
HIRING_SURGE_MULTIPLIER = 1.5
HIRING_SURGE_MIN_JOBS = 5
PIVOT_NEW_SHARE_THRESHOLD = 0.30   # 30% of permits in window
PIVOT_BASELINE_THRESHOLD = 0.10    # was under 10% in prior windows
ANOMALY_ZSCORE_THRESHOLD = 2.0
WINDOW_DAYS = 90
BASELINE_WINDOWS = 4               # how many prior windows to compare against


@dataclass(slots=True)
class TrendStats:
    windows_computed: int = 0
    signals_emitted: int = 0
    hiring_surges: int = 0
    pivots: int = 0
    expansions: int = 0
    anomalies: int = 0

    def __str__(self) -> str:
        return (
            f"{self.windows_computed} windows, {self.signals_emitted} signals "
            f"({self.hiring_surges} surges, {self.pivots} pivots, "
            f"{self.expansions} expansions, {self.anomalies} anomalies)"
        )


def _window_dedupe_key(signal_type: str, firm_id: Any, period_start: datetime) -> str:
    raw = f"{signal_type}:{firm_id}:{period_start.date().isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


async def _compute_window(
    session: AsyncSession,
    firm_id: Any,
    locality: str | None,
    period_start: datetime,
    period_end: datetime,
) -> TrendWindow:
    """Aggregate signals, permits, and job postings into one TrendWindow row."""
    # Permit count and valuation
    permit_rows = list(
        (
            await session.execute(
                select(Permit.permit_type, func.count().label("cnt"), func.sum(Permit.valuation_usd).label("val"))
                .where(
                    Permit.firm_id == firm_id,
                    Permit.filed_date >= period_start.date(),
                    Permit.filed_date < period_end.date(),
                )
                .group_by(Permit.permit_type)
            )
        ).all()
    )

    permit_count = sum(r.cnt for r in permit_rows)
    valuation_sum = float(sum(r.val or 0 for r in permit_rows))

    # Asset-class mix from permit types
    asset_class_mix = _derive_asset_class_mix(permit_rows)

    # Open job count at end of window
    open_jobs = (
        await session.scalar(
            select(func.count()).select_from(JobPosting).where(
                JobPosting.firm_id == firm_id,
                JobPosting.is_open.is_(True),
                JobPosting.first_seen_at < period_end,
            )
        )
    ) or 0

    # News/signal mention count
    signal_count = (
        await session.scalar(
            select(func.count()).select_from(Signal).where(
                Signal.firm_id == firm_id,
                Signal.occurred_at >= period_start,
                Signal.occurred_at < period_end,
                Signal.is_derived.is_(False),
            )
        )
    ) or 0

    # New projects announced in window
    from app.models.project import PropertyProject

    project_count = (
        await session.scalar(
            select(func.count()).select_from(PropertyProject).where(
                PropertyProject.firm_id == firm_id,
                PropertyProject.announced_date >= period_start.date(),
                PropertyProject.announced_date < period_end.date(),
            )
        )
    ) or 0

    # Upsert TrendWindow
    existing = (
        await session.execute(
            select(TrendWindow).where(
                TrendWindow.firm_id == firm_id,
                TrendWindow.locality == locality,
                TrendWindow.period_start == period_start,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        window = TrendWindow(
            firm_id=firm_id,
            locality=locality,
            period_start=period_start,
            period_end=period_end,
        )
        session.add(window)
    else:
        window = existing

    window.permit_count = permit_count
    window.valuation_sum_usd = valuation_sum
    window.open_job_count = open_jobs
    window.news_mention_count = signal_count
    window.new_project_count = project_count
    window.asset_class_mix = asset_class_mix

    return window


def _derive_asset_class_mix(permit_rows: list[Any]) -> dict[str, float]:
    """Map permit types to asset classes and compute share distribution."""
    TYPE_TO_CLASS: dict[str, str] = {
        "data center": AssetClass.DATA_CENTER.value,
        "multifamily": AssetClass.MULTIFAMILY.value,
        "residential": AssetClass.MULTIFAMILY.value,
        "industrial": AssetClass.INDUSTRIAL.value,
        "warehouse": AssetClass.LOGISTICS.value,
        "office": AssetClass.OFFICE.value,
        "retail": AssetClass.RETAIL.value,
        "mixed use": AssetClass.MIXED_USE.value,
        "hospitality": AssetClass.HOSPITALITY.value,
    }

    totals: dict[str, int] = {}
    for row in permit_rows:
        ptype = (row.permit_type or "").lower()
        asset = next(
            (cls for keyword, cls in TYPE_TO_CLASS.items() if keyword in ptype),
            None,
        )
        if asset:
            totals[asset] = totals.get(asset, 0) + row.cnt

    total = sum(totals.values())
    if total == 0:
        return {}
    return {k: round(v / total, 3) for k, v in totals.items()}


async def _emit_derived_signal(
    session: AsyncSession,
    *,
    firm_id: Any,
    signal_type: str,
    title: str,
    summary: str,
    score: float,
    locality: str | None,
    occurred_at: datetime,
    payload: dict[str, object],
    dedupe_key: str,
) -> bool:
    """Insert a derived signal; return True if new, False if duplicate."""
    existing = (
        await session.execute(
            select(Signal.id).where(Signal.dedupe_key == dedupe_key)
        )
    ).scalar_one_or_none()

    if existing is not None:
        return False

    session.add(
        Signal(
            firm_id=firm_id,
            signal_type=signal_type,
            title=title,
            summary=summary,
            score=score,
            locality=locality,
            occurred_at=occurred_at,
            detected_at=datetime.now(UTC),
            payload=payload,
            is_derived=True,
            dedupe_key=dedupe_key,
        )
    )
    return True


async def _detect_hiring_surge(
    session: AsyncSession,
    firm: Firm,
    current: TrendWindow,
    baseline_windows: list[TrendWindow],
    stats: TrendStats,
) -> None:
    if current.open_job_count < HIRING_SURGE_MIN_JOBS:
        return

    baseline_avg = (
        sum(w.open_job_count for w in baseline_windows) / len(baseline_windows)
        if baseline_windows
        else 0
    )
    if baseline_avg == 0 or current.open_job_count < baseline_avg * HIRING_SURGE_MULTIPLIER:
        return

    dedupe = _window_dedupe_key(SignalType.HIRING_SURGE, firm.id, current.period_start)
    emitted = await _emit_derived_signal(
        session,
        firm_id=firm.id,
        signal_type=SignalType.HIRING_SURGE,
        title=f"{firm.name} hiring surge",
        summary=(
            f"{firm.name} has {current.open_job_count} open roles — "
            f"{current.open_job_count / max(baseline_avg, 1):.1f}× the recent baseline."
        ),
        score=min(0.95, 0.6 + (current.open_job_count / max(baseline_avg, 1) - 1.5) * 0.1),
        locality=firm.county,
        occurred_at=current.period_start,
        payload={
            "current_jobs": current.open_job_count,
            "baseline_avg": round(baseline_avg, 1),
            "window_start": current.period_start.isoformat(),
        },
        dedupe_key=dedupe,
    )
    if emitted:
        stats.signals_emitted += 1
        stats.hiring_surges += 1


async def _detect_asset_class_pivot(
    session: AsyncSession,
    firm: Firm,
    current: TrendWindow,
    baseline_windows: list[TrendWindow],
    stats: TrendStats,
) -> None:
    if not current.asset_class_mix or not baseline_windows:
        return

    # Average baseline share per asset class
    baseline_mix: dict[str, float] = {}
    for w in baseline_windows:
        for cls, share in (w.asset_class_mix or {}).items():
            baseline_mix[cls] = baseline_mix.get(cls, 0) + share
    for cls in baseline_mix:
        baseline_mix[cls] /= len(baseline_windows)

    for asset_class, current_share in current.asset_class_mix.items():
        baseline_share = baseline_mix.get(asset_class, 0)
        if current_share >= PIVOT_NEW_SHARE_THRESHOLD and baseline_share < PIVOT_BASELINE_THRESHOLD:
            dedupe = _window_dedupe_key(
                f"{SignalType.ASSET_CLASS_PIVOT}:{asset_class}", firm.id, current.period_start
            )
            emitted = await _emit_derived_signal(
                session,
                firm_id=firm.id,
                signal_type=SignalType.ASSET_CLASS_PIVOT,
                title=f"{firm.name} pivoting to {asset_class.replace('_', ' ')}",
                summary=(
                    f"{firm.name}'s recent permits are {current_share:.0%} "
                    f"{asset_class.replace('_', ' ')} — up from {baseline_share:.0%} historically."
                ),
                score=0.75,
                locality=firm.county,
                occurred_at=current.period_start,
                payload={
                    "asset_class": asset_class,
                    "current_share": current_share,
                    "baseline_share": baseline_share,
                    "window_start": current.period_start.isoformat(),
                },
                dedupe_key=dedupe,
            )
            if emitted:
                stats.signals_emitted += 1
                stats.pivots += 1


async def _detect_geographic_expansion(
    session: AsyncSession,
    firm: Firm,
    current: TrendWindow,
    period_start: datetime,
    stats: TrendStats,
) -> None:
    # Find localities where this firm has permits in the current window
    current_localities = set(
        (
            await session.execute(
                select(Permit.locality).distinct().where(
                    Permit.firm_id == firm.id,
                    Permit.locality.is_not(None),
                    Permit.filed_date >= period_start.date(),
                )
            )
        ).scalars()
    )

    # Prior localities
    prior_localities = set(
        (
            await session.execute(
                select(Permit.locality).distinct().where(
                    Permit.firm_id == firm.id,
                    Permit.locality.is_not(None),
                    Permit.filed_date < period_start.date(),
                )
            )
        ).scalars()
    )

    new_localities = current_localities - prior_localities
    for locality in new_localities:
        dedupe = _window_dedupe_key(
            f"{SignalType.GEOGRAPHIC_EXPANSION}:{locality}", firm.id, period_start
        )
        emitted = await _emit_derived_signal(
            session,
            firm_id=firm.id,
            signal_type=SignalType.GEOGRAPHIC_EXPANSION,
            title=f"{firm.name} expanding into {locality}",
            summary=f"{firm.name} filed first permits in {locality} this quarter.",
            score=0.70,
            locality=locality,
            occurred_at=period_start,
            payload={"new_locality": locality, "window_start": period_start.isoformat()},
            dedupe_key=dedupe,
        )
        if emitted:
            stats.signals_emitted += 1
            stats.expansions += 1


async def _detect_permit_volume_anomaly(
    session: AsyncSession,
    firm: Firm,
    current: TrendWindow,
    baseline_windows: list[TrendWindow],
    stats: TrendStats,
) -> None:
    if len(baseline_windows) < 2:
        return

    counts = [w.permit_count for w in baseline_windows]
    import statistics

    mean = statistics.mean(counts)
    stdev = statistics.stdev(counts) if len(counts) > 1 else 0.0
    if stdev == 0:
        return

    zscore = (current.permit_count - mean) / stdev
    if zscore < ANOMALY_ZSCORE_THRESHOLD:
        return

    current.anomaly_score = round(zscore, 2)
    dedupe = _window_dedupe_key(SignalType.PERMIT_VOLUME_ANOMALY, firm.id, current.period_start)
    emitted = await _emit_derived_signal(
        session,
        firm_id=firm.id,
        signal_type=SignalType.PERMIT_VOLUME_ANOMALY,
        title=f"{firm.name} permit volume spike",
        summary=(
            f"{firm.name} filed {current.permit_count} permits this quarter — "
            f"{zscore:.1f}σ above baseline (avg {mean:.0f})."
        ),
        score=min(0.95, 0.65 + zscore * 0.05),
        locality=firm.county,
        occurred_at=current.period_start,
        payload={
            "permit_count": current.permit_count,
            "baseline_mean": round(mean, 1),
            "baseline_stdev": round(stdev, 1),
            "zscore": round(zscore, 2),
            "window_start": current.period_start.isoformat(),
        },
        dedupe_key=dedupe,
    )
    if emitted:
        stats.signals_emitted += 1
        stats.anomalies += 1


async def run_trend_detection(
    session: AsyncSession,
    *,
    window_days: int = WINDOW_DAYS,
    as_of: datetime | None = None,
) -> TrendStats:
    """Compute the current trend window for every tracked firm and derive signals.

    Idempotent: re-running over the same data emits no new signals because
    derived signals have dedupe_keys scoped to the window period.
    """
    stats = TrendStats()
    now = as_of or datetime.now(UTC)
    period_end = now
    period_start = now - timedelta(days=window_days)

    firms = list((await session.execute(select(Firm).where(Firm.is_active.is_(True)))).scalars())

    for firm in firms:
        # Compute current window
        current = await _compute_window(
            session, firm.id, firm.county, period_start, period_end
        )
        stats.windows_computed += 1

        # Load prior windows for baseline
        baseline = list(
            (
                await session.execute(
                    select(TrendWindow)
                    .where(
                        TrendWindow.firm_id == firm.id,
                        TrendWindow.period_start < period_start,
                    )
                    .order_by(TrendWindow.period_start.desc())
                    .limit(BASELINE_WINDOWS)
                )
            ).scalars()
        )

        # Run detectors
        await _detect_hiring_surge(session, firm, current, baseline, stats)
        await _detect_asset_class_pivot(session, firm, current, baseline, stats)
        await _detect_geographic_expansion(session, firm, current, period_start, stats)
        await _detect_permit_volume_anomaly(session, firm, current, baseline, stats)

    return stats
