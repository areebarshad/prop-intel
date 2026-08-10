"""Public contact endpoint — forwards landing page submissions to the owner."""

from __future__ import annotations

import sys

from fastapi import APIRouter, Request

from app.core.config import settings
from app.schemas import ContactRequest

router = APIRouter(prefix="/contact", tags=["contact"])


@router.post("", status_code=200)
async def submit_contact(body: ContactRequest, request: Request) -> dict[str, str]:
    subject = f"PropIntel Contact: {body.reason}"
    text_lines = [
        f"From: {body.email}",
        f"Reason: {body.reason}",
    ]
    if body.message:
        text_lines.append(f"Message: {body.message}")
    text = "\n".join(text_lines)

    if settings.notify.resend_api_key:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": (
                            f"Bearer {settings.notify.resend_api_key.get_secret_value()}"
                        )
                    },
                    json={
                        "from": settings.notify.from_email,
                        "to": ["areebarshad@vt.edu"],
                        "reply_to": body.email,
                        "subject": subject,
                        "text": text,
                    },
                )
        except Exception as exc:
            print(f"[CONTACT] Resend error: {exc}", file=sys.stderr)
    else:
        print(f"[CONTACT] {text}", file=sys.stderr)

    return {"status": "ok"}
