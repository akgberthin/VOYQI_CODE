"""
DMS-004 — F&I Menu & Compliance Console engine.

auto_issue_adverse_action is fully autonomous — ECOA/Reg B notices are
a legal deadline, not a judgment call. override_red_flag is the
deliberate exception: the FTC Red Flags Rule exists to prevent
identity theft, a genuine security matter, so it is the ONLY function
in this entire engine that refuses to proceed without a human-typed
reason, structurally, not by convention.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import AdverseActionNotice, RedFlagCheck


async def auto_issue_adverse_action(db: AsyncSession, tenant_id: str, deal_id: uuid.UUID, reason: str) -> AdverseActionNotice:
    notice = AdverseActionNotice(
        tenant_id=tenant_id, deal_id=deal_id, reason=reason,
        deadline=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(notice)
    await db.commit()
    await db.refresh(notice)
    return notice


async def create_red_flag_check(db: AsyncSession, tenant_id: str, deal_id: uuid.UUID, flags: list[str]) -> RedFlagCheck:
    check = RedFlagCheck(tenant_id=tenant_id, deal_id=deal_id, flags=flags)
    db.add(check)
    await db.commit()
    await db.refresh(check)
    return check


async def get_red_flag_check(db: AsyncSession, tenant_id: str, check_id: uuid.UUID) -> RedFlagCheck | None:
    result = await db.execute(select(RedFlagCheck).where(RedFlagCheck.id == check_id, RedFlagCheck.tenant_id == tenant_id))
    return result.scalar_one_or_none()


class MissingOverrideReasonError(ValueError):
    pass


async def override_red_flag(db: AsyncSession, tenant_id: str, check_id: uuid.UUID, overridden_by: str, reason: str) -> RedFlagCheck:
    """The one function in this engine with a hard human-input gate:
    identity-theft indicators under the FTC Red Flags Rule are a
    security matter, and this refuses to write an override without a
    real, non-empty, human-typed reason — no default, no bypass."""
    if not reason or not reason.strip():
        raise MissingOverrideReasonError("a human-typed override reason is required for a Red Flags Rule override")
    check = await get_red_flag_check(db, tenant_id, check_id)
    if check is None:
        raise ValueError(f"red flag check {check_id} not found")
    check.overridden_by = overridden_by
    check.override_reason = reason.strip()
    await db.commit()
    await db.refresh(check)
    return check
