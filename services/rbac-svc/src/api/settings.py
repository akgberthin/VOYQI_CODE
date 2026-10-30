import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import audit_engine, settings_engine

router = APIRouter(prefix="/api/v1/rbac/settings", tags=["settings"])


class SettingUpsert(BaseModel):
    category: str
    key: str
    value: dict
    updated_by: str = "super-admin"


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    category: str
    key: str
    value: dict
    updated_by: str


@router.post("", response_model=SettingOut, status_code=201)
async def upsert_setting(body: SettingUpsert, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous — a configuration change, not an access-control
    decision. Who is allowed to reach this endpoint at all is RBAC's
    job, enforced upstream of this engine, not inside it."""
    if body.category == "security":
        raise HTTPException(status_code=422, detail="Security settings are saved through /api/v1/rbac/security-policy, which validates them.")
    setting = await settings_engine.upsert_setting(db, tenant_id, body.category, body.key, body.value, body.updated_by)
    await audit_engine.record(db, tenant_id, "human", body.updated_by, "platform_setting.updated", body.category + "." + body.key)
    return setting


@router.get("", response_model=list[SettingOut])
async def list_settings(category: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await settings_engine.list_settings(db, tenant_id, category)


@router.delete("/{setting_id}")
async def delete_setting(setting_id: uuid.UUID, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    deleted = await settings_engine.delete_setting(db, tenant_id, setting_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Setting not found")
    await audit_engine.record(db, tenant_id, "human", actor_id, "platform_setting.deleted", str(setting_id))
    return {"deleted": True}
