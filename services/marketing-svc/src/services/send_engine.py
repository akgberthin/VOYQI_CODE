"""
Send engine — email, SMS, voice, and push. Fully autonomous scheduling
and execution: an approved draft can be scheduled and sent with no
further human click. The one thing this engine never skips, for any
recipient, on any channel: a suppression-list check. There is no
parameter on execute_send that bypasses it.
"""
import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import AnalyticsSnapshot, CalendarEntry, Campaign, CampaignStatus, ContentDraft, ContentStatus, ScheduledSend, SegmentMember, SendStatus
from src.services import delivery, suppression_engine


class DraftNotApprovedError(ValueError):
    pass


async def schedule_send(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID, content_draft_id: uuid.UUID, channel: str, scheduled_at: datetime) -> ScheduledSend:
    """Refuses to schedule a draft that hasn't cleared the guardrail —
    APPROVED is the only status this accepts, structurally, not a
    convention the caller has to remember to check first."""
    draft_result = await db.execute(select(ContentDraft).where(ContentDraft.id == content_draft_id, ContentDraft.tenant_id == tenant_id))
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        raise ValueError(f"content draft {content_draft_id} not found")
    if draft.status != ContentStatus.APPROVED:
        raise DraftNotApprovedError(f"content draft is {draft.status.value}, not approved — it must clear the guardrail (or a human review) before it can be scheduled")

    send = ScheduledSend(tenant_id=tenant_id, campaign_id=campaign_id, content_draft_id=content_draft_id, channel=channel, scheduled_at=scheduled_at)
    db.add(send)
    draft.status = ContentStatus.SCHEDULED

    await db.flush()
    db.add(CalendarEntry(tenant_id=tenant_id, channel=channel, title=f"Send: {draft.subject_line or draft.body_text[:40]}", scheduled_at=scheduled_at, reference_id=send.id, reference_type="scheduled_send"))

    await db.commit()
    await db.refresh(send)
    return send


class AlreadyExecutedError(Exception):
    pass


CONTACT_FIELD = {"email": "email", "sms": "phone", "voice": "phone", "push": "push_token"}


async def claim(db: AsyncSession, tenant_id: str, send_id: uuid.UUID) -> bool:
    """Atomically takes a scheduled send so it runs exactly once, whether a
    person presses Send or the scheduler gets there first."""
    result = await db.execute(
        update(ScheduledSend)
        .where(ScheduledSend.id == send_id, ScheduledSend.tenant_id == tenant_id,
               ScheduledSend.status == SendStatus.SCHEDULED, ScheduledSend.claimed_at.is_(None))
        .values(claimed_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return result.rowcount == 1


async def segment_recipients(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID, channel: str) -> list[tuple[str, str]]:
    """(contact, name) for every member of the campaign's segment reachable on this channel."""
    campaign = (await db.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id))).scalar_one_or_none()
    if campaign is None or campaign.audience_segment_id is None:
        return []
    field = CONTACT_FIELD.get(channel)
    members = (await db.execute(select(SegmentMember).where(SegmentMember.segment_id == campaign.audience_segment_id, SegmentMember.tenant_id == tenant_id))).scalars()
    return [(getattr(m, field), m.name) for m in members if field and getattr(m, field)]


def personalize(text: str, name: str) -> str:
    first = (name or "").split(" ")[0] or "there"
    return (text or "").replace("{first_name}", first).replace("{name}", name or first)


async def execute_send(db: AsyncSession, tenant_id: str, scheduled_send_id: uuid.UUID, recipient_contacts: list[str] | None = None, *, claimed: bool = False) -> ScheduledSend:
    """Delivers a scheduled send to the given contacts, or to the
    campaign's segment. Every recipient is checked against the
    suppression list first — no bypass parameter, no admin override
    anywhere in this signature. An entirely suppressed audience marks
    the send BLOCKED_SUPPRESSION rather than silently "succeeding"."""
    send = (await db.execute(select(ScheduledSend).where(ScheduledSend.id == scheduled_send_id, ScheduledSend.tenant_id == tenant_id))).scalar_one_or_none()
    if send is None:
        raise ValueError(f"scheduled send {scheduled_send_id} not found")
    if not claimed and not await claim(db, tenant_id, scheduled_send_id):
        await db.refresh(send)
        raise AlreadyExecutedError(f"send is already {send.status.value}" if send.status != SendStatus.SCHEDULED else "send is already being executed")
    await db.refresh(send)
    channel = send.channel.value
    draft = (await db.execute(select(ContentDraft).where(ContentDraft.id == send.content_draft_id))).scalar_one_or_none()

    recipients = [(c, "") for c in recipient_contacts] if recipient_contacts else await segment_recipients(db, tenant_id, send.campaign_id, channel)
    send.recipients_targeted = len(recipients)
    send.sent_at = datetime.now(timezone.utc)
    if not recipients:
        send.status = SendStatus.FAILED
        send.failure_detail = "No recipients: give the campaign a segment with contacts reachable on this channel."
        await db.commit()
        await db.refresh(send)
        return send

    allowed = []
    suppressed = 0
    for contact, name in recipients:
        if await suppression_engine.is_suppressed(db, tenant_id, channel, contact):
            suppressed += 1
        else:
            allowed.append((contact, name))

    gate = asyncio.Semaphore(get_settings().delivery_concurrency)

    async def one(contact: str, name: str) -> dict:
        async with gate:
            return await delivery.deliver(tenant_id, channel, contact, personalize(draft.body_text if draft else "", name),
                                          personalize(draft.subject_line if draft else "", name), f"marketing:{send.id}")

    results = await asyncio.gather(*(one(c, n) for c, n in allowed))
    real = sum(1 for r in results if r.get("status") == "sent" and r.get("delivery_mode") == "real_provider")
    console = sum(1 for r in results if r.get("status") == "sent" and r.get("delivery_mode") != "real_provider")
    opted_out = sum(1 for r in results if r.get("status") == "skipped_opted_out")
    failures = [r.get("error_message") or "failed" for r in results if r.get("status") == "failed"]

    send.recipients_suppressed = suppressed + opted_out
    send.recipients_failed = len(failures)
    send.recipients_console = console
    send.failure_detail = "; ".join(sorted(set(failures)))[:2000]
    if not allowed or (opted_out == len(allowed)):
        send.status = SendStatus.BLOCKED_SUPPRESSION
    elif real + console == 0:
        send.status = SendStatus.FAILED
    else:
        send.status = SendStatus.SENT
        if draft:
            draft.status = ContentStatus.SENT
    if real:
        # Only real deliveries count in analytics; console-mode sends are
        # visible on the send itself, never reported as reach.
        db.add(AnalyticsSnapshot(tenant_id=tenant_id, campaign_id=send.campaign_id, channel=send.channel, sent_count=real))
    await db.commit()
    await db.refresh(send)
    return send


async def due_sends(db: AsyncSession) -> list[ScheduledSend]:
    """Sends whose time has come, for campaigns that are running. A draft
    or paused campaign's sends wait (and go out once it's activated); a
    person can still press Send now."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ScheduledSend).join(Campaign, Campaign.id == ScheduledSend.campaign_id)
        .where(ScheduledSend.status == SendStatus.SCHEDULED, ScheduledSend.claimed_at.is_(None), ScheduledSend.scheduled_at <= now,
               Campaign.status == CampaignStatus.ACTIVE)
    )
    return list(result.scalars().all())


async def get_scheduled_send(db: AsyncSession, tenant_id: str, send_id: uuid.UUID) -> ScheduledSend | None:
    result = await db.execute(select(ScheduledSend).where(ScheduledSend.id == send_id, ScheduledSend.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_scheduled_sends(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID | None = None) -> list[ScheduledSend]:
    query = select(ScheduledSend).where(ScheduledSend.tenant_id == tenant_id)
    if campaign_id:
        query = query.where(ScheduledSend.campaign_id == campaign_id)
    result = await db.execute(query)
    return list(result.scalars().all())
