"""SAP-005 — Platform-wide audit log engine.

Every write in this service should call record() — see the API layer,
which wires this into every mutating endpoint rather than leaving it
to each caller to remember.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ActorType, AuditLogEntry


async def record(db: AsyncSession, tenant_id: str, actor_type: str, actor_id: str, action: str, resource: str, detail: str = "") -> AuditLogEntry:
    entry = AuditLogEntry(tenant_id=tenant_id, actor_type=actor_type, actor_id=actor_id, action=action, resource=resource, detail=detail)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def list_entries(db: AsyncSession, tenant_id: str, actor_type: str | None = None) -> list[AuditLogEntry]:
    query = select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id).order_by(AuditLogEntry.occurred_at.desc())
    if actor_type:
        query = query.where(AuditLogEntry.actor_type == actor_type)
    result = await db.execute(query)
    return list(result.scalars().all())
