"""Per-tenant channel rules: the voice calling window (TCPA-style quiet
hours, in the tenant's local time) and recurrence arithmetic."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import MarketingSettings

RECURRENCES = ("none", "daily", "weekly", "3x_week")


class PolicyError(Exception):
    pass


async def get(db: AsyncSession, tenant_id: str) -> MarketingSettings:
    row = (await db.execute(select(MarketingSettings).where(MarketingSettings.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        row = MarketingSettings(tenant_id=tenant_id, timezone="America/Chicago", voice_window_start=9, voice_window_end=20, updated_by="")
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def save(db: AsyncSession, tenant_id: str, values: dict, actor: str) -> MarketingSettings:
    row = await get(db, tenant_id)
    tz = values.get("timezone", row.timezone)
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise PolicyError(f"unknown timezone {tz!r}") from exc
    start, end = int(values.get("voice_window_start", row.voice_window_start)), int(values.get("voice_window_end", row.voice_window_end))
    if not (0 <= start < end <= 24):
        raise PolicyError("the calling window must start before it ends, within 0-24")
    if start < 8 or end > 21:
        raise PolicyError("calls are only allowed between 08:00 and 21:00 local time (TCPA)")
    row.timezone, row.voice_window_start, row.voice_window_end, row.updated_by = tz, start, end, actor
    await db.commit()
    await db.refresh(row)
    return row


def in_voice_window(settings: MarketingSettings, now: datetime | None = None) -> bool:
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(settings.timezone))
    return settings.voice_window_start <= local.hour < settings.voice_window_end


def next_window_start(settings: MarketingSettings, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(settings.timezone)
    local = now.astimezone(tz)
    candidate = local.replace(hour=settings.voice_window_start, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def next_occurrence(previous: datetime, recurrence: str) -> datetime | None:
    if recurrence == "daily":
        return previous + timedelta(days=1)
    if recurrence == "weekly":
        return previous + timedelta(days=7)
    if recurrence == "3x_week":  # Monday, Wednesday, Friday at the same time
        nxt = previous + timedelta(days=1)
        while nxt.weekday() not in (0, 2, 4):
            nxt += timedelta(days=1)
        return nxt
    return None
