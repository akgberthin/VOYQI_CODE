import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import audit_engine, media_engine

router = APIRouter(prefix="/api/v1/rbac/media", tags=["media"])


class MediaCreate(BaseModel):
    filename: str
    url: str
    alt_text: str = ""
    category: str = "general"
    uploaded_by: str = "super-admin"


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    filename: str
    url: str
    alt_text: str
    category: str
    uploaded_by: str


@router.post("", response_model=MediaOut, status_code=201)
async def create_asset(body: MediaCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    asset = await media_engine.create_asset(db, tenant_id, body.filename, body.url, body.alt_text, body.category, body.uploaded_by)
    await audit_engine.record(db, tenant_id, "human", body.uploaded_by, "media_asset.uploaded", body.filename)
    return asset


@router.get("", response_model=list[MediaOut])
async def list_assets(category: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await media_engine.list_assets(db, tenant_id, category)


@router.delete("/{asset_id}")
async def delete_asset(asset_id: uuid.UUID, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    deleted = await media_engine.delete_asset(db, tenant_id, asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Asset not found")
    await audit_engine.record(db, tenant_id, "human", actor_id, "media_asset.deleted", str(asset_id))
    return {"deleted": True}
