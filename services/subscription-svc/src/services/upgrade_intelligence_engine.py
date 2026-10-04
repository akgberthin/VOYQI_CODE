"""
Upgrade intelligence (PRD §11 "Upgrade moment" / §16.3 upgrade endpoint,
"AI recommends timing").

Detecting a pattern (a member repeatedly requesting vehicles above their
subscribed class) and drafting the case never touches billing, so it's
fully autonomous — the agent can create and offer a case on its own.
Actually enacting the upgrade changes what the member is billed every
month, which is exactly the financial-risk category from the platform's
automation directive: it always requires the member's own acceptance.
accept_upgrade_case is never called by the agent itself — only by a real
member action (or an ops person acting on the member's explicit
confirmation), the same separation HRM's benefits-cost-increase gate
uses, just applied to the member side instead of the employee side.
"""
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    AboveClassRequest,
    MobilityClass,
    Plan,
    Subscription,
    UpgradeCase,
    UpgradeCaseStatus,
)

UPGRADE_PATTERN_THRESHOLD = 3
UPGRADE_PATTERN_WINDOW_DAYS = 30


class UpgradeCaseNotOfferedError(ValueError):
    pass


class NoPlanForClassError(ValueError):
    pass


async def record_above_class_request(
    db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, subscription_id: uuid.UUID, subscribed_class: MobilityClass, requested_class: MobilityClass,
) -> tuple[AboveClassRequest, dict | None]:
    """Records the signal, then synchronously asks the Member
    Experience Agent to check the pattern — mirrors lifecycle_engine's
    winback call exactly. Never blocks: ai_orchestration_tools degrades
    to None on any failure rather than raising, so a slow or
    unreachable agent never holds up the member's own request."""
    from src.services import ai_orchestration_tools

    record = AboveClassRequest(tenant_id=tenant_id, member_profile_id=member_profile_id, subscribed_class=subscribed_class, requested_class=requested_class)
    db.add(record)
    await db.commit()
    await db.refresh(record)

    evaluation = ai_orchestration_tools.request_upgrade_pattern_evaluation(
        tenant_id, str(member_profile_id), str(subscription_id), subscribed_class.value
    )
    return record, evaluation


async def detect_upgrade_pattern(
    db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, window_days: int = UPGRADE_PATTERN_WINDOW_DAYS,
) -> tuple[MobilityClass | None, int, str]:
    """Deterministic — a real count against a real window, not a
    judgment call. Returns (most-requested class above the member's
    own, how many times, reasoning) or (None, 0, reasoning) if the
    threshold isn't met."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    result = await db.execute(
        select(AboveClassRequest).where(
            AboveClassRequest.tenant_id == tenant_id,
            AboveClassRequest.member_profile_id == member_profile_id,
            AboveClassRequest.created_at >= cutoff,
        )
    )
    requests = list(result.scalars().all())
    if len(requests) < UPGRADE_PATTERN_THRESHOLD:
        return None, len(requests), f"Only {len(requests)} above-class request(s) in the last {window_days} days — below the {UPGRADE_PATTERN_THRESHOLD}-request pattern threshold."

    counts = Counter(r.requested_class for r in requests)
    top_class, top_count = counts.most_common(1)[0]
    if top_count < UPGRADE_PATTERN_THRESHOLD:
        return None, len(requests), f"{len(requests)} above-class requests in the last {window_days} days, but no single class reached the {UPGRADE_PATTERN_THRESHOLD}-request threshold."
    return top_class, top_count, f"{top_count} requests for {top_class.value} in the last {window_days} days — pattern threshold met."


async def create_upgrade_case(
    db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, subscription_id: uuid.UUID,
    current_class: MobilityClass, recommended_class: MobilityClass, request_count: int, reasoning: str,
) -> UpgradeCase:
    case = UpgradeCase(
        tenant_id=tenant_id, member_profile_id=member_profile_id, subscription_id=subscription_id,
        current_class=current_class, recommended_class=recommended_class, above_class_request_count=request_count, reasoning=reasoning,
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return case


async def get_upgrade_case(db: AsyncSession, tenant_id: str, case_id: uuid.UUID) -> UpgradeCase | None:
    result = await db.execute(select(UpgradeCase).where(UpgradeCase.id == case_id, UpgradeCase.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_upgrade_cases(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID | None = None) -> list[UpgradeCase]:
    stmt = select(UpgradeCase).where(UpgradeCase.tenant_id == tenant_id)
    if member_profile_id:
        stmt = stmt.where(UpgradeCase.member_profile_id == member_profile_id)
    result = await db.execute(stmt.order_by(UpgradeCase.created_at.desc()))
    return list(result.scalars().all())


async def accept_upgrade_case(db: AsyncSession, tenant_id: str, case_id: uuid.UUID) -> tuple[UpgradeCase, Subscription]:
    """The one financial-risk step in this module — always a distinct,
    separately-authorized call from case creation. The agent that
    detects the pattern and offers the case never calls this itself."""
    case = await get_upgrade_case(db, tenant_id, case_id)
    if case is None or case.status != UpgradeCaseStatus.OFFERED:
        raise UpgradeCaseNotOfferedError("case not found or already resolved")

    result = await db.execute(select(Plan).where(Plan.tenant_id == tenant_id, Plan.mobility_class == case.recommended_class))
    plan = result.scalars().first()
    if plan is None:
        raise NoPlanForClassError(f"no plan on file for {case.recommended_class.value}")

    sub_result = await db.execute(select(Subscription).where(Subscription.id == case.subscription_id, Subscription.tenant_id == tenant_id))
    subscription = sub_result.scalar_one_or_none()
    if subscription is None:
        raise NoPlanForClassError("subscription not found")

    subscription.plan_id = plan.id
    case.status = UpgradeCaseStatus.ACCEPTED
    case.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(case)
    await db.refresh(subscription)
    return case, subscription


async def decline_upgrade_case(db: AsyncSession, tenant_id: str, case_id: uuid.UUID) -> UpgradeCase:
    case = await get_upgrade_case(db, tenant_id, case_id)
    if case is None or case.status != UpgradeCaseStatus.OFFERED:
        raise UpgradeCaseNotOfferedError("case not found or already resolved")
    case.status = UpgradeCaseStatus.DECLINED
    case.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(case)
    return case
