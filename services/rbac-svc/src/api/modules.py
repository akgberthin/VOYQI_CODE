import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import audit_engine, module_engine

router = APIRouter(prefix="/api/v1/rbac/module-grants", tags=["modules"])


class ModuleGrantCreate(BaseModel):
    module_key: str
    parent_module_key: str | None = None
    label: str = ""
    granted_by: str = "super-admin"


class ModuleGrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    module_key: str
    parent_module_key: str | None
    label: str
    enabled: bool
    granted_by: str


@router.post("", response_model=ModuleGrantOut, status_code=201)
async def grant_module(body: ModuleGrantCreate, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """A sub-section (parent_module_key set) can only be granted while
    its parent module is enabled — enforced in the engine, surfaced
    here as a 409, not silently allowed."""
    try:
        grant = await module_engine.grant_module(db, tenant_id, body.module_key, body.granted_by, body.parent_module_key, body.label)
    except module_engine.ParentModuleDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit_engine.record(db, tenant_id, "human", body.granted_by, "module_grant.enabled", body.module_key, f"parent={body.parent_module_key}" if body.parent_module_key else "")
    background_tasks.add_task(
        publish_event, "rbac.access", "panel.granted", tenant_id,
        {"module_key": body.module_key, "parent_module_key": body.parent_module_key, "granted_by": body.granted_by, "ai_initiated": body.granted_by.startswith(("ai:", "ai-orchestration"))},
    )
    return grant


@router.get("", response_model=list[ModuleGrantOut])
async def list_module_grants(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await module_engine.list_module_grants(db, tenant_id)


@router.post("/{module_key}/revoke", response_model=ModuleGrantOut)
async def revoke_module(module_key: str, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Revoking a parent module autonomously cascades to disable every
    one of its sub-sections too."""
    grant = await module_engine.revoke_module(db, tenant_id, module_key)
    if grant is None:
        raise HTTPException(status_code=404, detail="Module grant not found")
    await audit_engine.record(db, tenant_id, "human", actor_id, "module_grant.disabled", module_key)
    return grant
