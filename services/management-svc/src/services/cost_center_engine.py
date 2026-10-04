import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CostCenter, CostCenterCategory, CostCenterStatus


async def create_cost_center(
    db: AsyncSession, tenant_id: str, key: str, name: str, category: CostCenterCategory
) -> CostCenter:
    cost_center = CostCenter(tenant_id=tenant_id, key=key, name=name, category=category)
    db.add(cost_center)
    await db.commit()
    await db.refresh(cost_center)
    return cost_center


async def list_cost_centers(
    db: AsyncSession, tenant_id: str, category: CostCenterCategory | None = None
) -> list[CostCenter]:
    stmt = select(CostCenter).where(CostCenter.tenant_id == tenant_id)
    if category:
        stmt = stmt.where(CostCenter.category == category)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def deactivate_cost_center(
    db: AsyncSession, tenant_id: str, cost_center_id: uuid.UUID
) -> CostCenter | None:
    result = await db.execute(
        select(CostCenter).where(CostCenter.tenant_id == tenant_id, CostCenter.id == cost_center_id)
    )
    cost_center = result.scalar_one_or_none()
    if cost_center is None:
        return None
    cost_center.status = CostCenterStatus.INACTIVE
    await db.commit()
    await db.refresh(cost_center)
    return cost_center
