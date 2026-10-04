import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import employee_engine, notice_engine

router = APIRouter(prefix="/api/v1/hrm", tags=["notices"])


class NoticeCreate(BaseModel):
    title: str
    body: str
    posted_by: str = ""
    channels: str = "app"


class NoticeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    body: str
    posted_by: str
    channels: str
    created_at: datetime


class NoticeCommentCreate(BaseModel):
    employee_id: uuid.UUID
    body: str


class NoticeCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    notice_id: uuid.UUID
    employee_id: uuid.UUID
    body: str
    created_at: datetime


class NoteCreate(BaseModel):
    body: str
    author: str = ""


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    body: str
    author: str
    is_ai_generated: bool
    created_at: datetime
    updated_at: datetime


@router.post("/notices", response_model=NoticeOut, status_code=201)
async def create_notice(body: NoticeCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await notice_engine.create_notice(db, tenant_id, body.title, body.body, body.posted_by, body.channels)


@router.get("/notices", response_model=list[NoticeOut])
async def list_notices(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await notice_engine.list_notices(db, tenant_id)


@router.post("/notices/{notice_id}/comments", response_model=NoticeCommentOut, status_code=201)
async def add_notice_comment(notice_id: uuid.UUID, body: NoticeCommentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    notice = await notice_engine.get_notice(db, tenant_id, notice_id)
    if notice is None:
        raise HTTPException(status_code=404, detail="Notice not found")
    return await notice_engine.add_notice_comment(db, tenant_id, notice_id, body.employee_id, body.body)


@router.get("/notices/{notice_id}/comments", response_model=list[NoticeCommentOut])
async def list_notice_comments(notice_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await notice_engine.list_notice_comments(db, tenant_id, notice_id)


@router.post("/employees/{employee_id}/notes", response_model=NoteOut, status_code=201)
async def add_note(employee_id: uuid.UUID, body: NoteCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await notice_engine.add_note(db, tenant_id, employee_id, body.body, body.author, is_ai_generated=False)


@router.get("/employees/{employee_id}/notes", response_model=list[NoteOut])
async def list_notes_for_employee(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await notice_engine.list_notes_for_employee(db, tenant_id, employee_id)


class NoteAppend(BaseModel):
    additional_text: str
    author: str = ""


@router.post("/notes/{note_id}/append", response_model=NoteOut)
async def append_to_note(note_id: uuid.UUID, body: NoteAppend, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    note = await notice_engine.append_to_note(db, tenant_id, note_id, body.additional_text, body.author)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.post("/employees/{employee_id}/request-note-update", status_code=202)
async def request_note_update(
    employee_id: uuid.UUID, context: str, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    employee = await employee_engine.get_employee(db, tenant_id, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    background_tasks.add_task(
        publish_event,
        "hrm.notes",
        "hrm.note_update_requested",
        tenant_id,
        {"employee_id": str(employee_id), "context": context},
    )
    return {"employee_id": str(employee_id), "status": "note_update_requested"}


@router.post("/employees/{employee_id}/update-note-now")
async def update_note_now(employee_id: uuid.UUID, context: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first note logging the
    request endpoint (and case resolution) already trigger
    asynchronously."""
    from src.services import ai_orchestration_tools

    employee = await employee_engine.get_employee(db, tenant_id, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    response = ai_orchestration_tools.request_note_update(tenant_id, str(employee_id), context)
    if response is None:
        raise HTTPException(status_code=502, detail="Talent Agent unreachable")
    return response
