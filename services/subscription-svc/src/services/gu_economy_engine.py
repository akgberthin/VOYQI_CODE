"""
MOB-005 Growth Unit (GU) Economy Console engine.

Fraud detection is a deterministic rolling-window count (more than N
earn events for one member within M hours) — reliability matters more
than cleverness for something that flags a member's account. Flagged
entries still complete (the member still gets their GU) but the flag is
what routes to the Compliance/Audit Agent for review, the same
"complete the action, flag it separately for review" pattern used for
guardrail-violating marketing content elsewhere on the platform.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import (
    GUEarnTrigger,
    GULedgerEntry,
    GULedgerEntryType,
    MemberProfile,
    RedemptionCatalogItem,
)


class InsufficientGUBalanceError(ValueError):
    pass


async def _get_recent_earn_count(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, window_hours: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    result = await db.execute(
        select(func.count(GULedgerEntry.id)).where(
            GULedgerEntry.tenant_id == tenant_id, GULedgerEntry.member_profile_id == member_profile_id,
            GULedgerEntry.entry_type == GULedgerEntryType.EARN, GULedgerEntry.created_at >= since,
        )
    )
    return result.scalar_one()


async def earn(
    db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, trigger: GUEarnTrigger, amount: int
) -> GULedgerEntry:
    settings = get_settings()
    recent_count = await _get_recent_earn_count(db, tenant_id, member_profile_id, settings.gu_fraud_window_hours)
    flagged = recent_count >= settings.gu_fraud_earn_count_threshold

    entry = GULedgerEntry(
        tenant_id=tenant_id, member_profile_id=member_profile_id, entry_type=GULedgerEntryType.EARN,
        amount=amount, earn_trigger=trigger, flagged_for_fraud=flagged,
    )
    db.add(entry)

    profile_result = await db.execute(select(MemberProfile).where(MemberProfile.id == member_profile_id, MemberProfile.tenant_id == tenant_id))
    profile = profile_result.scalar_one_or_none()
    if profile:
        profile.gu_balance += amount

    await db.commit()
    await db.refresh(entry)
    return entry


async def redeem(
    db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, catalog_item_key: str
) -> GULedgerEntry:
    result = await db.execute(
        select(RedemptionCatalogItem).where(
            RedemptionCatalogItem.tenant_id == tenant_id, RedemptionCatalogItem.key == catalog_item_key,
            RedemptionCatalogItem.is_active.is_(True),
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise ValueError(f"redemption item '{catalog_item_key}' not found or inactive")

    profile_result = await db.execute(select(MemberProfile).where(MemberProfile.id == member_profile_id, MemberProfile.tenant_id == tenant_id))
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise ValueError(f"member {member_profile_id} not found")
    if profile.gu_balance < item.gu_cost:
        raise InsufficientGUBalanceError(
            f"member has {profile.gu_balance} GU, redemption requires {item.gu_cost}"
        )

    entry = GULedgerEntry(
        tenant_id=tenant_id, member_profile_id=member_profile_id, entry_type=GULedgerEntryType.REDEEM,
        amount=-item.gu_cost, redemption_item=item.key,
    )
    db.add(entry)
    profile.gu_balance -= item.gu_cost
    await db.commit()
    await db.refresh(entry)
    return entry


async def create_catalog_item(db: AsyncSession, tenant_id: str, key: str, name: str, gu_cost: int) -> RedemptionCatalogItem:
    item = RedemptionCatalogItem(tenant_id=tenant_id, key=key, name=name, gu_cost=gu_cost)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def get_ledger(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID) -> list[GULedgerEntry]:
    result = await db.execute(
        select(GULedgerEntry).where(GULedgerEntry.tenant_id == tenant_id, GULedgerEntry.member_profile_id == member_profile_id)
        .order_by(GULedgerEntry.created_at.desc())
    )
    return list(result.scalars().all())
