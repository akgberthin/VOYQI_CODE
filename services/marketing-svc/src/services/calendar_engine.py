"""
Calendar engine — the single view spanning every channel. Entries are
populated autonomously by send_engine.schedule_send and
social_engine.schedule_post, never created by hand here; this module
is read access plus one autonomous safety check: detecting whether a
newly-requested send time would over-message the same audience
segment too soon after another scheduled send on the same channel.
"""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import CalendarEntry


async def list_calendar(db: AsyncSession, tenant_id: str, channel: str | None = None, start: datetime | None = None, end: datetime | None = None) -> list[CalendarEntry]:
    query = select(CalendarEntry).where(CalendarEntry.tenant_id == tenant_id).order_by(CalendarEntry.scheduled_at)
    if channel:
        query = query.where(CalendarEntry.channel == channel)
    if start:
        query = query.where(CalendarEntry.scheduled_at >= start)
    if end:
        query = query.where(CalendarEntry.scheduled_at <= end)
    result = await db.execute(query)
    return list(result.scalars().all())


async def check_spacing_conflict(db: AsyncSession, tenant_id: str, channel: str, proposed_at: datetime) -> list[CalendarEntry]:
    """Autonomous over-messaging guard: returns any existing calendar
    entries on the same channel within the configured minimum spacing
    window of the proposed time. This doesn't block scheduling by
    itself (that's a judgment call about campaign intent this engine
    doesn't have enough context to make) — it surfaces the conflict so
    the caller (a human, or an agent with more context) can decide,
    the same way a calendar app flags a double-booking without
    refusing to create the second event."""
    settings = get_settings()
    window = timedelta(hours=settings.min_hours_between_sends_same_segment)
    entries = await list_calendar(db, tenant_id, channel=channel, start=proposed_at - window, end=proposed_at + window)
    return entries
