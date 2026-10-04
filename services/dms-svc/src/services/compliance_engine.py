"""
Compliance hold engine.

By design, this is the ONLY place on the platform where an item can
sit waiting for a human — and only for one of three reasons: OFAC,
Red Flags Rule, or a stolen-vehicle match. Nothing else is ever
written here; there is no generic "compliance event" path that could
accidentally grow a fourth category. Title disclosure, franchise
remediation, license renewals, and adverse-action notices are all
audit-log entries on their own tables (or Kafka events), never rows
in this table, because they never wait on a human at all.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ComplianceHold, ComplianceHoldType


async def create_hold(db: AsyncSession, tenant_id: str, hold_type: str, label: str, detail: str, risk_score: int) -> ComplianceHold:
    hold = ComplianceHold(tenant_id=tenant_id, type=hold_type, label=label, detail=detail, risk_score=risk_score)
    db.add(hold)
    await db.commit()
    await db.refresh(hold)
    return hold


async def list_open_holds(db: AsyncSession, tenant_id: str) -> list[ComplianceHold]:
    result = await db.execute(select(ComplianceHold).where(ComplianceHold.tenant_id == tenant_id, ComplianceHold.resolved == False))  # noqa: E712
    return list(result.scalars().all())


async def resolve_hold(db: AsyncSession, tenant_id: str, hold_id: uuid.UUID) -> ComplianceHold:
    result = await db.execute(select(ComplianceHold).where(ComplianceHold.id == hold_id, ComplianceHold.tenant_id == tenant_id))
    hold = result.scalar_one_or_none()
    if hold is None:
        raise ValueError(f"compliance hold {hold_id} not found")
    hold.resolved = True
    await db.commit()
    await db.refresh(hold)
    return hold
