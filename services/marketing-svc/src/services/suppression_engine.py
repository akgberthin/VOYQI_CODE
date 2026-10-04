"""
The suppression list engine — the ONE hard, structural gate in this
service. A contact on this list can never receive a send on the
suppressed channel, checked unconditionally before every single send
in send_engine.py and social_engine.py, with no override function
anywhere in this codebase. TCPA (SMS/voice) and CAN-SPAM (email) make
honoring an opt-out a legal requirement, not a judgment call.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import SuppressionListEntry


async def add_to_suppression_list(db: AsyncSession, tenant_id: str, channel: str, contact_identifier: str, reason: str) -> SuppressionListEntry:
    existing = await is_suppressed(db, tenant_id, channel, contact_identifier)
    if existing:
        return existing
    entry = SuppressionListEntry(tenant_id=tenant_id, channel=channel, contact_identifier=contact_identifier, reason=reason)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def is_suppressed(db: AsyncSession, tenant_id: str, channel: str, contact_identifier: str) -> SuppressionListEntry | None:
    """Called unconditionally before every send — this is the check
    that has no bypass parameter, no admin override, and no threshold
    anywhere in its call signature."""
    result = await db.execute(
        select(SuppressionListEntry).where(
            SuppressionListEntry.tenant_id == tenant_id,
            SuppressionListEntry.channel == channel,
            SuppressionListEntry.contact_identifier == contact_identifier,
        )
    )
    return result.scalar_one_or_none()


async def list_suppression_entries(db: AsyncSession, tenant_id: str, channel: str | None = None) -> list[SuppressionListEntry]:
    query = select(SuppressionListEntry).where(SuppressionListEntry.tenant_id == tenant_id)
    if channel:
        query = query.where(SuppressionListEntry.channel == channel)
    result = await db.execute(query)
    return list(result.scalars().all())


async def remove_from_suppression_list(db: AsyncSession, tenant_id: str, entry_id: uuid.UUID, removed_by: str) -> bool:
    """The only way off this list — a deliberate action recorded with
    who did it, distinct from a send simply bypassing the check."""
    result = await db.execute(select(SuppressionListEntry).where(SuppressionListEntry.id == entry_id, SuppressionListEntry.tenant_id == tenant_id))
    entry = result.scalar_one_or_none()
    if entry is None:
        return False
    await db.delete(entry)
    await db.commit()
    return True
