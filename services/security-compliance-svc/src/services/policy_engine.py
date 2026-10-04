import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CompliancePolicy, PolicyStatus


async def create_policy(db: AsyncSession, tenant_id: str, key: str, name: str, description: str) -> CompliancePolicy:
    policy = CompliancePolicy(tenant_id=tenant_id, key=key, name=name, description=description)
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


async def get_policy(db: AsyncSession, tenant_id: str, policy_id: uuid.UUID) -> CompliancePolicy | None:
    result = await db.execute(
        select(CompliancePolicy).where(CompliancePolicy.tenant_id == tenant_id, CompliancePolicy.id == policy_id)
    )
    return result.scalar_one_or_none()


async def list_policies(db: AsyncSession, tenant_id: str, status: PolicyStatus | None = None) -> list[CompliancePolicy]:
    stmt = select(CompliancePolicy).where(CompliancePolicy.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(CompliancePolicy.status == status)
    result = await db.execute(stmt.order_by(CompliancePolicy.created_at.desc()))
    return list(result.scalars().all())
