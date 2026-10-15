"""
MOB-007 Pause / Resume / Cancellation Manager engine.

Cancellation is a two-step flow on purpose: initiate_cancellation asks
the Member Experience Agent whether a win-back offer is warranted and
returns it to the member, but never blocks — if the agent call fails or
recommends nothing, the member proceeds straight to
finalize_cancellation. The member's right to cancel is never contingent
on an AI call succeeding.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    MemberProfile,
    PauseHistoryEntry,
    Plan,
    Subscription,
    SubscriptionStatus,
)
from src.services import ai_orchestration_tools


async def get_subscription(db: AsyncSession, tenant_id: str, subscription_id: uuid.UUID) -> Subscription | None:
    result = await db.execute(select(Subscription).where(Subscription.id == subscription_id, Subscription.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def pause_subscription(db: AsyncSession, tenant_id: str, subscription_id: uuid.UUID, resume_at: datetime) -> Subscription | None:
    result = await db.execute(select(Subscription).where(Subscription.id == subscription_id, Subscription.tenant_id == tenant_id))
    sub = result.scalar_one_or_none()
    if sub is None or sub.status != SubscriptionStatus.ACTIVE:
        return None
    sub.status = SubscriptionStatus.PAUSED
    sub.paused_at = datetime.now(timezone.utc)
    sub.resume_at = resume_at
    db.add(PauseHistoryEntry(tenant_id=tenant_id, subscription_id=subscription_id, member_profile_id=sub.member_profile_id, paused_at=sub.paused_at))
    await db.commit()
    await db.refresh(sub)
    return sub


async def resume_subscription(db: AsyncSession, tenant_id: str, subscription_id: uuid.UUID) -> Subscription | None:
    result = await db.execute(select(Subscription).where(Subscription.id == subscription_id, Subscription.tenant_id == tenant_id))
    sub = result.scalar_one_or_none()
    if sub is None or sub.status != SubscriptionStatus.PAUSED:
        return None
    sub.status = SubscriptionStatus.ACTIVE
    sub.paused_at = None
    sub.resume_at = None

    open_entry_result = await db.execute(
        select(PauseHistoryEntry)
        .where(PauseHistoryEntry.subscription_id == subscription_id, PauseHistoryEntry.tenant_id == tenant_id, PauseHistoryEntry.resumed_at.is_(None))
        .order_by(PauseHistoryEntry.paused_at.desc())
    )
    open_entry = open_entry_result.scalars().first()
    if open_entry is not None:
        open_entry.resumed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(sub)
    return sub


async def count_pauses_in_window(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, window_days: int = 365) -> int:
    """A real count from real history, not a stand-in — this is what
    makes 'pause_count_last_year' in churn-risk context an actual
    signal rather than a placeholder."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    result = await db.execute(
        select(PauseHistoryEntry).where(
            PauseHistoryEntry.tenant_id == tenant_id,
            PauseHistoryEntry.member_profile_id == member_profile_id,
            PauseHistoryEntry.paused_at >= cutoff,
        )
    )
    return len(list(result.scalars().all()))


async def initiate_cancellation(db: AsyncSession, tenant_id: str, subscription_id: uuid.UUID, reason: str) -> dict:
    result = await db.execute(select(Subscription).where(Subscription.id == subscription_id, Subscription.tenant_id == tenant_id))
    sub = result.scalar_one_or_none()
    if sub is None:
        return {"found": False, "winback_offer": None}

    winback = ai_orchestration_tools.request_winback_evaluation(
        tenant_id, str(sub.member_profile_id), str(subscription_id), reason
    )
    return {"found": True, "subscription_id": str(subscription_id), "winback_offer": winback}


async def finalize_cancellation(
    db: AsyncSession, tenant_id: str, subscription_id: uuid.UUID, reason: str, exit_survey: dict | None = None
) -> Subscription | None:
    result = await db.execute(select(Subscription).where(Subscription.id == subscription_id, Subscription.tenant_id == tenant_id))
    sub = result.scalar_one_or_none()
    if sub is None or sub.status == SubscriptionStatus.CANCELLED:
        return None
    sub.status = SubscriptionStatus.CANCELLED
    sub.cancelled_at = datetime.now(timezone.utc)
    sub.cancellation_reason = reason
    sub.exit_survey = exit_survey or {}
    await db.commit()
    await db.refresh(sub)
    return sub


async def accept_winback_offer(db: AsyncSession, tenant_id: str, subscription_id: uuid.UUID) -> Subscription | None:
    result = await db.execute(select(Subscription).where(Subscription.id == subscription_id, Subscription.tenant_id == tenant_id))
    sub = result.scalar_one_or_none()
    if sub is None:
        return None
    sub.win_back_offer_extended = True
    await db.commit()
    await db.refresh(sub)
    return sub


async def create_subscription(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, plan_id: uuid.UUID) -> Subscription:
    """Fully autonomous: creating the subscription and syncing the
    member's own mobility_class to match are the same event, not two
    steps a caller has to remember to chain — a member profile whose
    class doesn't match their active subscription is a real data
    inconsistency, not a display nuance."""
    sub = Subscription(tenant_id=tenant_id, member_profile_id=member_profile_id, plan_id=plan_id)
    db.add(sub)

    plan_result = await db.execute(select(Plan).where(Plan.id == plan_id, Plan.tenant_id == tenant_id))
    plan = plan_result.scalar_one_or_none()
    if plan:
        member_result = await db.execute(select(MemberProfile).where(MemberProfile.id == member_profile_id, MemberProfile.tenant_id == tenant_id))
        member = member_result.scalar_one_or_none()
        if member:
            member.mobility_class = plan.mobility_class

    await db.commit()
    await db.refresh(sub)
    return sub
