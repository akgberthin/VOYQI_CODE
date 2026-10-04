import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import PolicyStatus, RiskPolicy


async def create_policy(
    db: AsyncSession, tenant_id: str, policy_number: str, policy_type: str, insured_name: str, coverage_amount_usd: float
) -> RiskPolicy:
    policy = RiskPolicy(
        tenant_id=tenant_id, policy_number=policy_number, policy_type=policy_type,
        insured_name=insured_name, coverage_amount_usd=coverage_amount_usd,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


async def get_policy(db: AsyncSession, tenant_id: str, policy_id: uuid.UUID) -> RiskPolicy | None:
    result = await db.execute(select(RiskPolicy).where(RiskPolicy.tenant_id == tenant_id, RiskPolicy.id == policy_id))
    return result.scalar_one_or_none()


async def list_policies(db: AsyncSession, tenant_id: str, status: PolicyStatus | None = None) -> list[RiskPolicy]:
    stmt = select(RiskPolicy).where(RiskPolicy.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(RiskPolicy.status == status)
    result = await db.execute(stmt.order_by(RiskPolicy.created_at.desc()))
    return list(result.scalars().all())


async def set_policy_status(db: AsyncSession, tenant_id: str, policy_id: uuid.UUID, status: PolicyStatus) -> RiskPolicy | None:
    policy = await get_policy(db, tenant_id, policy_id)
    if policy is None:
        return None
    policy.status = status
    await db.commit()
    await db.refresh(policy)
    return policy
