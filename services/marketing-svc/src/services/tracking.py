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


def arm(contact: str, test_id: uuid.UUID, split_b_pct: int = 50) -> str:
    """A stable split: the same contact always gets the same arm; about
    split_b_pct% of contacts get B."""
    h = int.from_bytes(hashlib.sha256(f"{test_id}|{contact.lower()}".encode()).digest()[:2], "big") % 100
    return "b" if h < split_b_pct else "a"


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


# -- Provider outcomes and voice retries --------------------------------

def call_outcome(report: dict) -> str | None:
    """answered / voicemail / no-answer / busy / failed, or None while the call is not over."""
    status = report.get("provider_status")
    if status == "completed":
        return "voicemail" if str(report.get("answered_by") or "").startswith("machine") else "answered"
    return status if status in ("no-answer", "busy", "failed", "canceled") else None


async def sync_outcomes(session_factory, now: datetime | None = None) -> int:
    """Reads delivery and call outcomes back from notification-svc for
    recent texts and calls. An answered call counts as a pickup (an open);
    an unanswered one is scheduled again while attempts remain."""
    from datetime import timedelta

    from src.models import ContentDraft
    from src.services import delivery

    now = now or datetime.now(timezone.utc)
    updated = 0
    async with session_factory() as db:
        pending = list((await db.execute(select(MessageRecipient).where(
            MessageRecipient.channel.in_(("voice", "sms")), MessageRecipient.outcome == "", MessageRecipient.notification_id.is_not(None),
            MessageRecipient.delivery_mode == "real_provider", MessageRecipient.sent_at >= now - timedelta(days=3)))).scalars())
        by_tenant: dict[str, list[MessageRecipient]] = {}
        for r in pending:
            by_tenant.setdefault(r.tenant_id, []).append(r)
        for tenant_id, rows in by_tenant.items():
            try:
                reports = {x["id"]: x for x in await delivery.statuses(tenant_id, [str(r.notification_id) for r in rows])}
            except Exception:  # noqa: BLE001 - notification-svc down: try on the next pass
                continue
            for r in rows:
                rep = reports.get(str(r.notification_id))
                if not rep:
                    continue
                if rep.get("recording_url"):
                    r.recording_url = rep["recording_url"]
                if r.channel == "sms":
                    if rep.get("provider_status") in ("delivered", "undelivered", "failed"):
                        r.outcome = "delivered" if rep["provider_status"] == "delivered" else "undelivered"
                        updated += 1
                    continue
                outcome = call_outcome(rep)
                if outcome is None:
                    continue
                r.outcome = outcome
                updated += 1
                if outcome == "answered":
                    await db.commit()
                    await record(db, r.id, "open")  # a pickup
                    continue
                draft = await db.get(ContentDraft, r.content_draft_id) if r.content_draft_id else None
                o = (draft.options if draft else None) or {}
                if r.retry_payload and r.attempts < int(o.get("max_attempts") or 1):
                    r.next_attempt_at = now + timedelta(hours=int(o.get("retry_hours") or 4))
        await db.commit()
    return updated


async def retry_due(session_factory, now: datetime | None = None) -> int:
    """Calls again the contacts whose last call was not answered, once the
    retry time has come and the calling window is open. Suppression is
    checked again: someone who opted out meanwhile is never called back."""
    from src.services import channel_policy, delivery, suppression_engine

    now = now or datetime.now(timezone.utc)
    done = 0
    async with session_factory() as db:
        due = list((await db.execute(select(MessageRecipient).where(MessageRecipient.next_attempt_at.is_not(None), MessageRecipient.next_attempt_at <= now))).scalars())
        for r in due:
            policy = await channel_policy.get(db, r.tenant_id)
            if not channel_policy.in_voice_window(policy, now):
                r.next_attempt_at = channel_policy.next_window_start(policy, now)
                continue
            r.next_attempt_at = None
            if await suppression_engine.is_suppressed(db, r.tenant_id, r.channel, r.contact):
                r.outcome = "suppressed"
                continue
            p = r.retry_payload or {}
            result = await delivery.deliver(r.tenant_id, r.channel, r.contact, p.get("body", ""), p.get("subject", ""), f"marketing:{r.send_id}:retry", p.get("options") or {})
            r.attempts += 1
            if result.get("status") == "sent":
                r.outcome, r.delivery_mode = "", result.get("delivery_mode") or ""
                try:
                    r.notification_id = uuid.UUID(str(result.get("id")))
                except ValueError:
                    r.notification_id = None
            else:
                r.outcome = "failed"
            done += 1
        await db.commit()
    return done


async def outcomes(db: AsyncSession, tenant_id: str, send_id: uuid.UUID) -> dict:
    """Per send: delivered / answered / voicemail / no answer, retries waiting, recordings."""
    rows = list((await db.execute(select(MessageRecipient).where(MessageRecipient.tenant_id == tenant_id, MessageRecipient.send_id == send_id))).scalars())
    counts: dict[str, int] = {}
    for r in rows:
        key = r.outcome or ("awaiting report" if r.delivery_mode == "real_provider" else "console only")
        counts[key] = counts.get(key, 0) + 1
    return {"messages": len(rows), "outcomes": counts, "opened": sum(1 for r in rows if r.opened_at), "clicked": sum(1 for r in rows if r.clicked_at),
            "retries_scheduled": sum(1 for r in rows if r.next_attempt_at), "attempts": sum(r.attempts for r in rows),
            "recordings": [{"contact": r.contact, "url": r.recording_url} for r in rows if r.recording_url][:100]}
