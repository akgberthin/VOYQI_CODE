import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from src.core.database import get_db
from src.services import audit_engine, tenant_engine
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/rbac/tenants", tags=["tenants"])


class TenantCreate(BaseModel):
    tenant_key: str
    name: str
    subsidiary: str
    actor_id: str = "super-admin"


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_key: str
    name: str
    subsidiary: str
    status: str


@router.post("", response_model=TenantOut, status_code=201)
async def create_tenant(body: TenantCreate, db: AsyncSession = Depends(get_db)):
    tenant = await tenant_engine.create_tenant(db, body.tenant_key, body.name, body.subsidiary)
    await audit_engine.record(db, body.tenant_key, "human", body.actor_id, "tenant.created", str(tenant.id), f"subsidiary={body.subsidiary}")
    return tenant


@router.get("", response_model=list[TenantOut])
async def list_tenants(db: AsyncSession = Depends(get_db)):
    return await tenant_engine.list_tenants(db)


@router.post("/{tenant_id}/suspend", response_model=TenantOut)
async def suspend_tenant(tenant_id: uuid.UUID, actor_id: str = "super-admin", db: AsyncSession = Depends(get_db)):
    tenant = await tenant_engine.suspend_tenant(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    await audit_engine.record(db, tenant.tenant_key, "human", actor_id, "tenant.suspended", str(tenant.id))
    return tenant


@router.post("/{tenant_id}/reactivate", response_model=TenantOut)
async def reactivate_tenant(tenant_id: uuid.UUID, actor_id: str = "super-admin", db: AsyncSession = Depends(get_db)):
    tenant = await tenant_engine.reactivate_tenant(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    await audit_engine.record(db, tenant.tenant_key, "human", actor_id, "tenant.reactivated", str(tenant.id))
    return tenant
