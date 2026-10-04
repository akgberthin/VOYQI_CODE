"""SAP-001 — Global Tenant Directory engine."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Tenant, TenantStatus


async def create_tenant(db: AsyncSession, tenant_key: str, name: str, subsidiary: str) -> Tenant:
    tenant = Tenant(tenant_key=tenant_key, name=name, subsidiary=subsidiary)
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return tenant


async def list_tenants(db: AsyncSession) -> list[Tenant]:
    result = await db.execute(select(Tenant))
    return list(result.scalars().all())


async def get_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


async def suspend_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    tenant = await get_tenant(db, tenant_id)
    if tenant is None:
        return None
    tenant.status = TenantStatus.SUSPENDED
    await db.commit()
    await db.refresh(tenant)
    return tenant


async def reactivate_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    tenant = await get_tenant(db, tenant_id)
    if tenant is None:
        return None
    tenant.status = TenantStatus.ACTIVE
    await db.commit()
    await db.refresh(tenant)
    return tenant
