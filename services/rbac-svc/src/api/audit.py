import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import audit_engine

router = APIRouter(prefix="/api/v1/rbac/audit-log", tags=["audit"])


class AuditLogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    actor_type: str
    actor_id: str
    action: str
    resource: str
    detail: str


@router.get("", response_model=list[AuditLogEntryOut])
async def list_entries(actor_type: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Platform-wide, both human and AI actors — distinct from
    ai-orchestration-svc's DecisionLog, which is AI-decision-specific."""
    return await audit_engine.list_entries(db, tenant_id, actor_type)
