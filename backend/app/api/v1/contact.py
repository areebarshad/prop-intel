"""Public contact endpoint — persists landing page submissions and notifies the owner."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import rate_limited_ip
from app.db.session import get_db
from app.models.lead import Lead
from app.schemas import ContactRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contact", tags=["contact"])


def _fallback_full_name(email: str) -> str:
    """The form does not always collect a name, but `leads.full_name` is NOT NULL.

    The local part of the address is the best available stand-in, and losing the
    whole submission over a missing optional field would be the worse trade.
    """
    return email.split("@", 1)[0][:200]


@router.post("", status_code=200)
async def submit_contact(
    body: ContactRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limited_ip),
) -> dict[str, str]:
    # `reason` has no column of its own; folding it into the notes keeps the
    # whole submission in one place rather than dropping it.
    note_lines = [f"Reason: {body.reason}"]
    if body.use_case_notes:
        note_lines.append(body.use_case_notes)

    lead = Lead(
        full_name=body.full_name or _fallback_full_name(body.work_email),
        work_email=body.work_email,
        company=body.company,
        role=body.role,
        use_case_notes="\n".join(note_lines),
        ip_address=request.client.host if request.client else None,
    )
    db.add(lead)
    # Committed before the email is attempted: a Resend outage must not cost us
    # the lead.
    await db.commit()

    subject = f"PropIntel Contact: {body.reason}"
    text_lines = [
        f"From: {body.full_name or '(no name)'} <{body.work_email}>",
        f"Reason: {body.reason}",
    ]
    if body.company:
        text_lines.append(f"Company: {body.company}")
    if body.role:
        text_lines.append(f"Role: {body.role}")
    if body.use_case_notes:
        text_lines.append(f"Message: {body.use_case_notes}")
    text = "\n".join(text_lines)

    recipients = settings.notify.unique_contact_recipients
    if not settings.notify.resend_api_key:
        logger.info("contact notification skipped (no Resend API key); lead %s: %s", lead.id, text)
    elif not recipients:
        logger.warning(
            "contact notification skipped for lead %s: no contact_recipients configured", lead.id
        )
    else:
        import httpx

        # The lead is already committed above, so every failure path here only
        # costs the notification — never the submission itself.
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": (
                            f"Bearer {settings.notify.resend_api_key.get_secret_value()}"
                        )
                    },
                    json={
                        "from": settings.notify.from_email,
                        "to": recipients,
                        "reply_to": body.work_email,
                        "subject": subject,
                        "text": text,
                    },
                )
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "resend error %s for lead %s: %s",
                exc.response.status_code,
                lead.id,
                exc.response.text[:200],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("contact notification failed for lead %s: %s", lead.id, exc)

    return {"status": "ok"}
