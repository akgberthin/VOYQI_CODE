"""
SAP-006 — Platform settings engine: payment gateways, branding/UI-UX,
feature flags, and general configuration.

Fully autonomous: every write here is a configuration change, not an
access-control decision — none of it touches who can do what, which is
RBAC/ABAC's exclusive domain. There is deliberately no approval gate on
changing a payment gateway's public key or a brand color; the gate
that actually matters (who is allowed to reach this settings page at
all) is enforced by RBAC permissions on the endpoint, not by anything
in this engine.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import PlatformSetting


async def upsert_setting(db: AsyncSession, tenant_id: str, category: str, key: str, value: dict, updated_by: str) -> PlatformSetting:
    result = await db.execute(select(PlatformSetting).where(PlatformSetting.tenant_id == tenant_id, PlatformSetting.category == category, PlatformSetting.key == key))
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = value
        existing.updated_by = updated_by
        await db.commit()
        await db.refresh(existing)
        return existing

    setting = PlatformSetting(tenant_id=tenant_id, category=category, key=key, value=value, updated_by=updated_by)
    db.add(setting)
    await db.commit()
    await db.refresh(setting)
    return setting


async def list_settings(db: AsyncSession, tenant_id: str, category: str | None = None) -> list[PlatformSetting]:
    query = select(PlatformSetting).where(PlatformSetting.tenant_id == tenant_id)
    if category:
        query = query.where(PlatformSetting.category == category)
    result = await db.execute(query)
    return list(result.scalars().all())


async def delete_setting(db: AsyncSession, tenant_id: str, setting_id: uuid.UUID) -> bool:
    result = await db.execute(select(PlatformSetting).where(PlatformSetting.id == setting_id, PlatformSetting.tenant_id == tenant_id))
    setting = result.scalar_one_or_none()
    if setting is None:
        return False
    await db.delete(setting)
    await db.commit()
    return True
