import base64
import binascii
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import media_engine

router = APIRouter(prefix="/api/v1/marketing", tags=["media"])


class Upload(BaseModel):
    title: str = ""
    content_type: str
    data_base64: str
    created_by: str = "marketing-admin"


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    content_type: str
    source: str
    created_by: str
    created_at: datetime | None
    url: str = ""


def _out(asset) -> AssetOut:
    out = AssetOut.model_validate(asset)
    out.url = media_engine.public_url(asset.id) or ""
    return out


@router.post("/media", response_model=AssetOut, status_code=201)
async def upload(body: Upload, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        data = base64.b64decode(body.data_base64, validate=True)
        asset = await media_engine.store(db, tenant_id, data, body.content_type, body.title, "upload", body.created_by)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="data_base64 isn't valid base64") from exc
    except media_engine.InvalidMedia as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _out(asset)


@router.get("/media", response_model=list[AssetOut])
async def list_media(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return [_out(a) for a in await media_engine.list_assets(db, tenant_id)]


@router.get("/public/media/{asset_id}")
async def serve(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Public (no tenant, no session): social platforms download post
    images from here. Ids are random UUIDs."""
    asset = await media_engine.get(db, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="not found")
    return Response(content=asset.data, media_type=asset.content_type, headers={"Cache-Control": "public, max-age=86400"})
