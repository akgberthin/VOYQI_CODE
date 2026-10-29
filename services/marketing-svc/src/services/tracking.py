"""Open and click tracking for marketing messages.

Each delivered message gets a MessageRecipient row. Emails carry a 1×1
pixel and links rewritten through /public/t/c/…; SMS links are rewritten
the same way. Tokens and link targets are HMAC-signed, so a tracking URL
can't be forged for another recipient or used to redirect anywhere else.

The first open/click of a message adds to the campaign's analytics (one
snapshot row per event) and, when the message was part of an A/B test,
to that arm — which then evaluates itself.
"""
import base64
import hashlib
import hmac
import html
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import ABTest, ABTestStatus, AnalyticsSnapshot, Channel, MessageRecipient

PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")
HREF_RE = re.compile(r'href=(["\'])(https?://[^"\']+)\1', re.I)


def _secret() -> bytes:
    s = get_settings()
    return (s.tracking_secret or s.internal_token or "voyqi-dev-tracking-secret").encode()


def _sig(value: str) -> str:
    return hmac.new(_secret(), value.encode(), hashlib.sha256).hexdigest()[:24]


def token(recipient_id: uuid.UUID) -> str:
    return f"{recipient_id.hex}.{_sig(recipient_id.hex)}"


def parse_token(tok: str) -> uuid.UUID | None:
    rid, _, sig = (tok or "").partition(".")
    if not rid or not hmac.compare_digest(sig, _sig(rid)):
        return None
    try:
        return uuid.UUID(hex=rid)
    except ValueError:
        return None


def link_sig(tok: str, url: str) -> str:
    return _sig(f"{tok}|{url}")


def _base() -> str:
    return get_settings().public_base_url.rstrip("/") + "/api/v1/marketing/public/t"


def click_url(tok: str, url: str) -> str:
    return f"{_base()}/c/{tok}?u={quote(url, safe='')}&s={link_sig(tok, url)}"


def open_url(tok: str) -> str:
    return f"{_base()}/o/{tok}.gif"


def instrument_email(body: str, tok: str) -> str:
    """Tracked links and an open pixel. Plain text is turned into simple
    HTML (escaped, line breaks kept) so it can carry the pixel."""
    if "<" not in body:
        text = html.escape(body)
        text = URL_RE.sub(lambda m: f'<a href="{click_url(tok, html.unescape(m.group(0)))}">{m.group(0)}</a>', text)
        body = f'<div style="white-space:pre-wrap;font-family:Arial,sans-serif">{text}</div>'
    else:
        body = HREF_RE.sub(lambda m: f'href={m.group(1)}{html.escape(click_url(tok, html.unescape(m.group(2))))}{m.group(1)}', body)
    pixel = f'<img src="{open_url(tok)}" width="1" height="1" alt="" style="display:none">'
    return body.replace("</body>", pixel + "</body>") if "</body>" in body else body + pixel


def instrument_sms(body: str, tok: str) -> str:
    return URL_RE.sub(lambda m: click_url(tok, m.group(0)), body)


async def _count(db: AsyncSession, r: MessageRecipient, opened: bool, clicked: bool) -> None:
    db.add(AnalyticsSnapshot(tenant_id=r.tenant_id, campaign_id=r.campaign_id, channel=Channel(r.channel),
                             sent_count=0, open_count=int(opened), click_count=int(clicked)))
    if r.ab_test_id and r.variant in ("a", "b"):
        test = await db.get(ABTest, r.ab_test_id)
        if test and test.status != ABTestStatus.CONCLUDED and ((test.metric == "open_rate" and opened) or (test.metric == "click_rate" and clicked)):
            if r.variant == "a":
                test.variant_a_converted += 1
            else:
                test.variant_b_converted += 1


async def record(db: AsyncSession, recipient_id: uuid.UUID, kind: str) -> MessageRecipient | None:
    """kind: "open" or "click" (a click implies the message was opened)."""
    r = await db.get(MessageRecipient, recipient_id)
    if r is None:
        return None
    now = datetime.now(timezone.utc)
    first_open = r.opened_at is None
    first_click = kind == "click" and r.clicked_at is None
    if first_open:
        r.opened_at = now
    r.opens += 1
    if kind == "click":
        r.clicks += 1
        if first_click:
            r.clicked_at = now
    if first_open or first_click:
        await _count(db, r, first_open, first_click)
    await db.commit()
    if (first_open or first_click) and r.ab_test_id:
        from src.services import ab_test_engine

        await ab_test_engine.evaluate_test(db, r.tenant_id, r.ab_test_id)
    return r


async def ab_for(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID, draft_id: uuid.UUID | None) -> ABTest | None:
    """The running A/B test this send's draft is variant A of, if any."""
    if draft_id is None:
        return None
    return (await db.execute(select(ABTest).where(
        ABTest.tenant_id == tenant_id, ABTest.campaign_id == campaign_id, ABTest.variant_a_draft_id == draft_id,
        ABTest.status != ABTestStatus.CONCLUDED))).scalars().first()


def arm(contact: str, test_id: uuid.UUID) -> str:
    """A stable 50/50 split: the same contact always gets the same arm."""
    return "a" if hashlib.sha256(f"{test_id}|{contact.lower()}".encode()).digest()[0] < 128 else "b"


async def send_times(db: AsyncSession, tenant_id: str, tz_name: str) -> dict:
    """Email open rate by weekday and hour sent (tenant's local time),
    from real deliveries only."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    grid: dict[tuple[int, int], list[int]] = {}
    rows = (await db.execute(select(MessageRecipient).where(
        MessageRecipient.tenant_id == tenant_id, MessageRecipient.channel == "email", MessageRecipient.status == "sent",
        MessageRecipient.delivery_mode == "real_provider"))).scalars()
    for r in rows:
        sent = r.sent_at if r.sent_at.tzinfo else r.sent_at.replace(tzinfo=timezone.utc)
        local = sent.astimezone(tz)
        cell = grid.setdefault((local.weekday(), local.hour), [0, 0])
        cell[0] += 1
        cell[1] += 1 if r.opened_at else 0
    cells = [{"weekday": d, "hour": h, "sent": s, "opened": o, "open_rate": round(o / s, 4)} for (d, h), (s, o) in sorted(grid.items())]
    best = sorted((c for c in cells if c["sent"] >= 20), key=lambda c: c["open_rate"], reverse=True)[:3]
    return {"timezone": tz_name, "cells": cells, "best": best, "messages": sum(c["sent"] for c in cells),
            "note": None if cells else "No tracked email deliveries yet — best times appear once real sends are opened."}
