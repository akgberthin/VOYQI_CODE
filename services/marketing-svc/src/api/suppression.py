import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import suppression_engine

router = APIRouter(prefix="/api/v1/marketing/suppression", tags=["suppression"])


class SuppressionCreate(BaseModel):
    channel: str
    contact_identifier: str
    reason: str


class SuppressionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    channel: str
    contact_identifier: str
    reason: str


@router.post("", response_model=SuppressionOut, status_code=201)
async def add_to_suppression_list(body: SuppressionCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await suppression_engine.add_to_suppression_list(db, tenant_id, body.channel, body.contact_identifier, body.reason)


@router.get("", response_model=list[SuppressionOut])
async def list_suppression_entries(channel: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await suppression_engine.list_suppression_entries(db, tenant_id, channel)


@router.get("/check")
async def check_suppressed(channel: str, contact_identifier: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    entry = await suppression_engine.is_suppressed(db, tenant_id, channel, contact_identifier)
    return {"suppressed": entry is not None, "reason": entry.reason.value if entry else None}


@router.delete("/{entry_id}")
async def remove_from_suppression_list(entry_id: uuid.UUID, removed_by: str = "marketing-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    removed = await suppression_engine.remove_from_suppression_list(db, tenant_id, entry_id, removed_by)
    if not removed:
        raise HTTPException(status_code=404, detail="Suppression entry not found")
    return {"removed": True}
