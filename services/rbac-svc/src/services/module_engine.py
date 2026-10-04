"""SAP-004 — Panel & module distribution engine.

Fully autonomous: granting or revoking a module is a provisioning
action, not an access-control policy decision — it determines which
panels a tenant's users can reach, not what permissions those users
hold once there (that's RBAC/ABAC's job).

A sub-section (parent_module_key set) can only be enabled while its
parent module is enabled — enforced here, not just in the UI. Revoking
a parent autonomously cascades to disable every one of its
sub-sections too, since a sub-section reachable through a disabled
parent isn't reachable at all.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ModuleGrant


class ParentModuleDisabledError(ValueError):
    pass


async def _get_grant(db: AsyncSession, tenant_id: str, module_key: str) -> ModuleGrant | None:
    result = await db.execute(select(ModuleGrant).where(ModuleGrant.tenant_id == tenant_id, ModuleGrant.module_key == module_key))
    return result.scalar_one_or_none()


async def grant_module(db: AsyncSession, tenant_id: str, module_key: str, granted_by: str, parent_module_key: str | None = None, label: str = "") -> ModuleGrant:
    if parent_module_key:
        parent = await _get_grant(db, tenant_id, parent_module_key)
        if parent is None or not parent.enabled:
            raise ParentModuleDisabledError(f"parent module '{parent_module_key}' must be granted and enabled before its sub-section can be")

    existing = await _get_grant(db, tenant_id, module_key)
    if existing:
        existing.enabled = True
        existing.granted_by = granted_by
        await db.commit()
        await db.refresh(existing)
        return existing

    grant = ModuleGrant(tenant_id=tenant_id, module_key=module_key, parent_module_key=parent_module_key, label=label or module_key, granted_by=granted_by)
    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    return grant


async def revoke_module(db: AsyncSession, tenant_id: str, module_key: str) -> ModuleGrant | None:
    """Revoking a parent autonomously cascades to every sub-section
    grant beneath it — a sub-section reachable only through a disabled
    parent isn't meaningfully enabled either way."""
    grant = await _get_grant(db, tenant_id, module_key)
    if grant is None:
        return None
    grant.enabled = False

    children_result = await db.execute(select(ModuleGrant).where(ModuleGrant.tenant_id == tenant_id, ModuleGrant.parent_module_key == module_key))
    for child in children_result.scalars().all():
        child.enabled = False

    await db.commit()
    await db.refresh(grant)
    return grant


async def list_module_grants(db: AsyncSession, tenant_id: str) -> list[ModuleGrant]:
    result = await db.execute(select(ModuleGrant).where(ModuleGrant.tenant_id == tenant_id))
    return list(result.scalars().all())
