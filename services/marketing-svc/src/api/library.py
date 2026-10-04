import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import library_engine

router = APIRouter(prefix="/api/v1/marketing/library", tags=["library"])


class LibraryItemCreate(BaseModel):
    title: str
    channel: str
    body_text: str
    tags: list[str] = []
    created_by: str = "marketing-admin"


class LibraryItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    channel: str
    body_text: str
    tags: list
    created_by: str


@router.post("", response_model=LibraryItemOut, status_code=201)
async def create_library_item(body: LibraryItemCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await library_engine.create_library_item(db, tenant_id, body.title, body.channel, body.body_text, body.created_by, body.tags)


@router.get("", response_model=list[LibraryItemOut])
async def list_library_items(channel: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await library_engine.list_library_items(db, tenant_id, channel)


@router.delete("/{item_id}")
async def delete_library_item(item_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    deleted = await library_engine.delete_library_item(db, tenant_id, item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Library item not found")
    return {"deleted": True}
