import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import notes_engine

router = APIRouter(prefix="/api/v1/marketing/notes", tags=["notes"])


class NoteCreate(BaseModel):
    author: str
    body: str
    campaign_id: uuid.UUID | None = None


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    author: str
    body: str
    campaign_id: uuid.UUID | None


@router.post("", response_model=NoteOut, status_code=201)
async def create_note(body: NoteCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await notes_engine.create_note(db, tenant_id, body.author, body.body, body.campaign_id)


@router.get("", response_model=list[NoteOut])
async def list_notes(campaign_id: uuid.UUID | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await notes_engine.list_notes(db, tenant_id, campaign_id)


@router.delete("/{note_id}")
async def delete_note(note_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    deleted = await notes_engine.delete_note(db, tenant_id, note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": True}
