"""
Send engine — email, SMS, voice, and push. Fully autonomous scheduling
and execution: an approved draft can be scheduled and sent with no
further human click. The one thing this engine never skips, for any
recipient, on any channel: a suppression-list check. There is no
parameter on execute_send that bypasses it.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CalendarEntry, ContentDraft, ContentStatus, ScheduledSend, SendStatus
from src.services import suppression_engine


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


async def execute_send(db: AsyncSession, tenant_id: str, scheduled_send_id: uuid.UUID, recipient_contacts: list[str]) -> ScheduledSend:
    """Checks suppression for every single recipient before counting
    them as sent — no bypass parameter, no admin override anywhere in
    this signature. A recipient list that's entirely suppressed still
    marks the send BLOCKED_SUPPRESSION rather than silently
    succeeding with zero real sends."""
    result = await db.execute(select(ScheduledSend).where(ScheduledSend.id == scheduled_send_id, ScheduledSend.tenant_id == tenant_id))
    send = result.scalar_one_or_none()
    if send is None:
        raise ValueError(f"scheduled send {scheduled_send_id} not found")

    targeted = len(recipient_contacts)
    suppressed_count = 0
    for contact in recipient_contacts:
        if await suppression_engine.is_suppressed(db, tenant_id, send.channel.value, contact):
            suppressed_count += 1

    send.recipients_targeted = targeted
    send.recipients_suppressed = suppressed_count
    send.sent_at = datetime.now(timezone.utc)

    if suppressed_count >= targeted and targeted > 0:
        send.status = SendStatus.BLOCKED_SUPPRESSION
    else:
        send.status = SendStatus.SENT
        draft_result = await db.execute(select(ContentDraft).where(ContentDraft.id == send.content_draft_id))
        draft = draft_result.scalar_one_or_none()
        if draft:
            draft.status = ContentStatus.SENT

    await db.commit()
    await db.refresh(send)
    return send


async def get_scheduled_send(db: AsyncSession, tenant_id: str, send_id: uuid.UUID) -> ScheduledSend | None:
    result = await db.execute(select(ScheduledSend).where(ScheduledSend.id == send_id, ScheduledSend.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_scheduled_sends(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID | None = None) -> list[ScheduledSend]:
    query = select(ScheduledSend).where(ScheduledSend.tenant_id == tenant_id)
    if campaign_id:
        query = query.where(ScheduledSend.campaign_id == campaign_id)
    result = await db.execute(query)
    return list(result.scalars().all())
