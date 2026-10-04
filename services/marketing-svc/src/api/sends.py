import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import send_engine

router = APIRouter(prefix="/api/v1/marketing/sends", tags=["sends"])


class ScheduleSendRequest(BaseModel):
    campaign_id: uuid.UUID
    content_draft_id: uuid.UUID
    channel: str
    scheduled_at: datetime


class ScheduledSendOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    campaign_id: uuid.UUID
    content_draft_id: uuid.UUID
    channel: str
    scheduled_at: datetime
    status: str
    recipients_targeted: int
    recipients_suppressed: int
    sent_at: datetime | None


@router.post("", response_model=ScheduledSendOut, status_code=201)
async def schedule_send(body: ScheduleSendRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Refuses (409) a draft that hasn't cleared the guardrail or a
    human review — APPROVED is the only status this accepts."""
    try:
        return await send_engine.schedule_send(db, tenant_id, body.campaign_id, body.content_draft_id, body.channel, body.scheduled_at)
    except send_engine.DraftNotApprovedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("", response_model=list[ScheduledSendOut])
async def list_scheduled_sends(campaign_id: uuid.UUID | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await send_engine.list_scheduled_sends(db, tenant_id, campaign_id)


class ExecuteSendRequest(BaseModel):
    recipient_contacts: list[str]


@router.post("/{send_id}/execute", response_model=ScheduledSendOut)
async def execute_send(send_id: uuid.UUID, body: ExecuteSendRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Checks the suppression list for every recipient before
    counting them as sent — no bypass parameter anywhere in this
    request body."""
    try:
        return await send_engine.execute_send(db, tenant_id, send_id, body.recipient_contacts)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
