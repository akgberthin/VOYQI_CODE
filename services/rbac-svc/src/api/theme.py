import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import audit_engine, theme_engine

router = APIRouter(prefix="/api/v1/rbac/themes", tags=["theme"])


class ThemeCreate(BaseModel):
    name: str
    seed_defaults: bool = True
    created_by: str = "super-admin"


class ThemeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    is_active: bool


class ThemeColorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    theme_id: uuid.UUID
    color_key: str
    hex_value: str


@router.post("", response_model=ThemeOut, status_code=201)
async def create_theme(body: ThemeCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    theme = await theme_engine.create_theme(db, tenant_id, body.name, body.seed_defaults)
    await audit_engine.record(db, tenant_id, "human", body.created_by, "theme.created", theme.name)
    return theme


@router.get("", response_model=list[ThemeOut])
async def list_themes(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await theme_engine.list_themes(db, tenant_id)


@router.get("/active", response_model=ThemeOut)
async def get_active_theme(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    theme = await theme_engine.get_active_theme(db, tenant_id)
    if theme is None:
        raise HTTPException(status_code=404, detail="No active theme set")
    return theme


@router.get("/{theme_id}/colors", response_model=list[ThemeColorOut])
async def get_theme_colors(theme_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await theme_engine.get_theme_colors(db, tenant_id, theme_id)


class ColorSet(BaseModel):
    color_key: str
    hex_value: str
    updated_by: str = "super-admin"


@router.post("/{theme_id}/colors", response_model=ThemeColorOut)
async def set_theme_color(theme_id: uuid.UUID, body: ColorSet, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    color = await theme_engine.set_theme_color(db, tenant_id, theme_id, body.color_key, body.hex_value)
    await audit_engine.record(db, tenant_id, "human", body.updated_by, "theme_color.updated", f"{theme_id}.{body.color_key}", body.hex_value)
    return color


@router.post("/{theme_id}/activate", response_model=ThemeOut)
async def activate_theme(theme_id: uuid.UUID, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous — deactivates every other theme for this
    tenant in the same call, so exactly one theme is ever active."""
    theme = await theme_engine.activate_theme(db, tenant_id, theme_id)
    if theme is None:
        raise HTTPException(status_code=404, detail="Theme not found")
    await audit_engine.record(db, tenant_id, "human", actor_id, "theme.activated", theme.name)
    return theme


@router.delete("/{theme_id}")
async def delete_theme(theme_id: uuid.UUID, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    deleted = await theme_engine.delete_theme(db, tenant_id, theme_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Theme not found")
    await audit_engine.record(db, tenant_id, "human", actor_id, "theme.deleted", str(theme_id))
    return {"deleted": True}
