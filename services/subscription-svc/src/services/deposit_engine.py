"""
Deposit holds for Luxury Plus / Performance Plus (PRD §16.6, "NOT YET
BUILT" until this module).

Placement and full release are a fixed table lookup by class — deterministic
policy, not a judgment call, so both execute autonomously with no AI
involvement at all, the same principle as payroll math elsewhere on this
platform. Capturing part or all of a deposit (damage, contract violation)
is different in kind: the amount and justification are a real judgment
call with a genuine, discretionary financial consequence to the member.
That is the one step this module holds for human review — not because
deposits are inherently risky, but because *discretionary* capture is.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    DEPOSIT_HOLD_AMOUNTS_USD,
    DepositHold,
    DepositHoldStatus,
    MobilityClass,
)


class DepositAmountUndefinedError(ValueError):
    pass


class DepositNotPlacedError(ValueError):
    pass


class DepositNotPendingCaptureError(ValueError):
    pass


def deposit_amount_for_class(mobility_class: MobilityClass) -> float:
    amount = DEPOSIT_HOLD_AMOUNTS_USD.get(mobility_class.value)
    if amount is None:
        raise DepositAmountUndefinedError(f"{mobility_class.value} does not require a deposit hold")
    return amount


async def place_deposit_hold(
    db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, mobility_class: MobilityClass, subscription_id: uuid.UUID | None = None,
) -> DepositHold:
    """Deterministic — the amount is a fixed table lookup by class, so
    this never needs an AI decision or a human approval step."""
    amount = deposit_amount_for_class(mobility_class)
    hold = DepositHold(
        tenant_id=tenant_id, member_profile_id=member_profile_id, subscription_id=subscription_id,
        mobility_class=mobility_class, amount_usd=amount,
    )
    db.add(hold)
    await db.commit()
    await db.refresh(hold)
    return hold


async def get_deposit_hold(db: AsyncSession, tenant_id: str, hold_id: uuid.UUID) -> DepositHold | None:
    result = await db.execute(select(DepositHold).where(DepositHold.id == hold_id, DepositHold.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_deposit_holds(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID | None = None) -> list[DepositHold]:
    stmt = select(DepositHold).where(DepositHold.tenant_id == tenant_id)
    if member_profile_id:
        stmt = stmt.where(DepositHold.member_profile_id == member_profile_id)
    result = await db.execute(stmt.order_by(DepositHold.placed_at.desc()))
    return list(result.scalars().all())


async def release_deposit_hold(db: AsyncSession, tenant_id: str, hold_id: uuid.UUID) -> DepositHold:
    """Full return, nothing captured — returning someone's own money is
    never the risky direction, so this is autonomous too."""
    hold = await get_deposit_hold(db, tenant_id, hold_id)
    if hold is None or hold.status != DepositHoldStatus.PLACED:
        raise DepositNotPlacedError("hold not found or not in a releasable state")
    hold.status = DepositHoldStatus.RELEASED
    hold.released_at = datetime.now(timezone.utc)
    hold.resolved_at = hold.released_at
    await db.commit()
    await db.refresh(hold)
    return hold


async def request_capture(db: AsyncSession, tenant_id: str, hold_id: uuid.UUID, amount_usd: float, reason: str) -> tuple[DepositHold, dict | None]:
    """Always lands in CAPTURE_PENDING_REVIEW — there is no path,
    AI-scored or manual, that skips human approval before money is
    discretionarily taken from a member. approve_capture is the sole
    action that actually resolves it. Also synchronously asks
    compliance_audit_agent for a risk assessment, mirroring
    subscription-svc's own winback/upgrade-pattern calls — but this
    call's success or failure changes nothing about the mandatory
    human gate above; it only adds a risk signal for the reviewer."""
    from src.services import ai_orchestration_tools

    hold = await get_deposit_hold(db, tenant_id, hold_id)
    if hold is None or hold.status != DepositHoldStatus.PLACED:
        raise DepositNotPlacedError("hold not found or not in a capturable state")
    if amount_usd <= 0 or amount_usd > hold.amount_usd:
        raise ValueError(f"capture amount must be between $0 and the held ${hold.amount_usd}")
    hold.status = DepositHoldStatus.CAPTURE_PENDING_REVIEW
    hold.captured_amount_usd = amount_usd
    hold.capture_reason = reason
    await db.commit()
    await db.refresh(hold)

    evaluation = ai_orchestration_tools.request_deposit_capture_review(tenant_id, str(hold_id), amount_usd, reason)
    return hold, evaluation


async def approve_capture(db: AsyncSession, tenant_id: str, hold_id: uuid.UUID) -> DepositHold:
    hold = await get_deposit_hold(db, tenant_id, hold_id)
    if hold is None or hold.status != DepositHoldStatus.CAPTURE_PENDING_REVIEW:
        raise DepositNotPendingCaptureError("hold not found or not pending a capture decision")
    hold.status = DepositHoldStatus.CAPTURED
    hold.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(hold)
    return hold


async def reject_capture(db: AsyncSession, tenant_id: str, hold_id: uuid.UUID) -> DepositHold:
    """Rejecting a capture request releases the remainder — and, since
    nothing is actually being taken, the full amount — back to PLACED
    so it can still be released normally, or re-requested with a
    corrected amount/reason."""
    hold = await get_deposit_hold(db, tenant_id, hold_id)
    if hold is None or hold.status != DepositHoldStatus.CAPTURE_PENDING_REVIEW:
        raise DepositNotPendingCaptureError("hold not found or not pending a capture decision")
    hold.status = DepositHoldStatus.PLACED
    hold.captured_amount_usd = 0.0
    hold.capture_reason = ""
    await db.commit()
    await db.refresh(hold)
    return hold
