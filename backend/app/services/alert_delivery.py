"""Alert fan-out: match new signals against standing alert rules and deliver.

Delivery channels:
  email  — via Resend (PROPINTEL_NOTIFY__RESEND_API_KEY must be set)
  slack  — via an incoming webhook (PROPINTEL_NOTIFY__SLACK_WEBHOOK_URL must be set)

Each (alert, signal) pair is recorded in `alert_deliveries` with a unique
constraint, so re-running the fan-out over an overlapping window never
sends a duplicate notification.

Delivery records are written BEFORE notifications are dispatched. This ensures
at-most-once semantics: if the process crashes after the DB write but before
the HTTP call the record exists and prevents a duplicate send on the next run.

Typical usage:
    stats = await deliver_alerts(session)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.alert import Alert, AlertDelivery
from app.models.signal import Signal

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_HOURS = 25
_SLACK_WEBHOOK_PREFIX = "https://hooks.slack.com/"


@dataclass(slots=True)
class DeliveryStats:
    alerts_checked: int = 0
    signals_matched: int = 0
    deliveries_sent: int = 0
    deliveries_skipped: int = 0
    errors: int = 0

    def __str__(self) -> str:
        return (
            f"{self.alerts_checked} alerts, {self.signals_matched} matches, "
            f"{self.deliveries_sent} sent, {self.deliveries_skipped} skipped, "
            f"{self.errors} errors"
        )


def _signal_matches(signal: Signal, filters: dict) -> bool:
    firm_ids: list[str] = filters.get("firm_ids") or []
    if firm_ids and str(signal.firm_id) not in firm_ids:
        return False

    signal_types: list[str] = filters.get("signal_types") or []
    if signal_types and signal.signal_type not in signal_types:
        return False

    localities: list[str] = filters.get("localities") or []
    if localities and signal.locality not in localities:
        return False

    asset_classes: list[str] = filters.get("asset_classes") or []
    if asset_classes and signal.asset_class not in asset_classes:
        return False

    min_score: float = filters.get("min_score") or 0.0
    if signal.score < min_score:
        return False

    return True


def _email_body(alert: Alert, signals: list[Signal]) -> str:
    lines = [f"PropIntel alert: {alert.name}\n"]
    for sig in signals[:10]:
        date_str = sig.occurred_at.strftime("%b %-d") if sig.occurred_at else "recent"
        lines.append(f"• [{date_str}] {sig.title} (score={sig.score:.2f})")
    if len(signals) > 10:
        lines.append(f"… and {len(signals) - 10} more.")
    return "\n".join(lines)


def _slack_blocks(alert: Alert, signals: list[Signal]) -> list[dict]:
    text = f"*PropIntel — {alert.name}*\n"
    for sig in signals[:5]:
        date_str = sig.occurred_at.strftime("%b %-d") if sig.occurred_at else "recent"
        text += f"\n• [{date_str}] {sig.title}"
    if len(signals) > 5:
        text += f"\n_… and {len(signals) - 5} more_"
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]


async def _send_email(alert: Alert, signals: list[Signal]) -> bool:
    if not settings.notify.resend_api_key:
        log.debug("email delivery skipped: PROPINTEL_NOTIFY__RESEND_API_KEY not set")
        return False
    if not alert.channel_target:
        log.warning("email delivery skipped for alert %s: channel_target is null", alert.id)
        return False
    import httpx

    body = _email_body(alert, signals)
    payload = {
        "from": settings.notify.from_email,
        "to": [alert.channel_target],
        "subject": f"[PropIntel] {alert.name}",
        "text": body,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.notify.resend_api_key.get_secret_value()}"
                },
            )
            resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("email send failed for alert %s: %s", alert.id, exc)
        return False


async def _send_slack(alert: Alert, signals: list[Signal]) -> bool:
    webhook = alert.channel_target or (
        settings.notify.slack_webhook_url.get_secret_value()
        if settings.notify.slack_webhook_url
        else None
    )
    if not webhook:
        log.debug("slack delivery skipped: no webhook configured")
        return False
    if not webhook.startswith(_SLACK_WEBHOOK_PREFIX):
        log.warning(
            "slack delivery rejected for alert %s: webhook URL is not a hooks.slack.com address",
            alert.id,
        )
        return False
    import httpx

    payload = {"blocks": _slack_blocks(alert, signals)}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(webhook, json=payload)
            resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("slack send failed for alert %s: %s", alert.id, exc)
        return False


async def deliver_alerts(
    session: AsyncSession,
    *,
    since: datetime | None = None,
) -> DeliveryStats:
    """Fan out new signals to matching active alerts.

    Uses PostgreSQL INSERT ... ON CONFLICT DO NOTHING (via the unique constraint
    on alert_deliveries) to guarantee at-most-once delivery per (alert, signal).

    Delivery records are inserted as 'pending' BEFORE the HTTP send, so the
    record exists in the transaction before any external side-effect occurs.
    """
    stats = DeliveryStats()
    now = datetime.now(UTC)
    lookback_start = since or (now - timedelta(hours=DEFAULT_LOOKBACK_HOURS))

    new_signals: list[Signal] = list(
        (
            await session.execute(
                select(Signal).where(Signal.detected_at >= lookback_start)
            )
        ).scalars()
    )
    if not new_signals:
        return stats

    alerts: list[Alert] = list(
        (await session.execute(select(Alert).where(Alert.is_active.is_(True)))).scalars()
    )
    stats.alerts_checked = len(alerts)

    for alert in alerts:
        matched = [s for s in new_signals if _signal_matches(s, alert.filters)]
        if not matched:
            continue

        existing_sig_ids = set(
            (
                await session.execute(
                    select(AlertDelivery.signal_id).where(
                        AlertDelivery.alert_id == alert.id,
                        AlertDelivery.signal_id.in_([s.id for s in matched]),
                    )
                )
            ).scalars()
        )
        new_matches = [s for s in matched if s.id not in existing_sig_ids]
        stats.deliveries_skipped += len(matched) - len(new_matches)

        if not new_matches:
            continue

        stats.signals_matched += len(new_matches)

        # Write pending delivery records BEFORE sending any notification.
        # ON CONFLICT DO NOTHING prevents duplicate records on overlapping runs.
        for sig in new_matches:
            stmt = pg_insert(AlertDelivery).values(
                alert_id=alert.id,
                signal_id=sig.id,
                channel=alert.channel,
                status="pending",
                attempts=1,
            ).on_conflict_do_nothing(constraint="uq_alert_delivery")
            await session.execute(stmt)
        await session.flush()

        # Now attempt delivery.
        success = False
        try:
            if alert.channel == "email":
                success = await _send_email(alert, new_matches)
            elif alert.channel == "slack":
                success = await _send_slack(alert, new_matches)
            else:
                log.warning("unknown delivery channel %r for alert %s", alert.channel, alert.id)
        except Exception as exc:  # noqa: BLE001
            log.error("delivery error for alert %s: %s", alert.id, exc)
            stats.errors += 1

        # Update the pending records with the actual delivery outcome.
        delivery_status = "sent" if success else "failed"
        sig_ids = [s.id for s in new_matches]
        await session.execute(
            update(AlertDelivery)
            .where(
                AlertDelivery.alert_id == alert.id,
                AlertDelivery.signal_id.in_(sig_ids),
                AlertDelivery.status == "pending",
            )
            .values(
                status=delivery_status,
                delivered_at=now if success else None,
            )
        )

        if success:
            alert.last_fired_at = now
            alert.fire_count += len(new_matches)
            stats.deliveries_sent += len(new_matches)
        else:
            stats.errors += 1

    return stats
