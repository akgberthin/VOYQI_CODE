import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import calendar_engine

router = APIRouter(prefix="/api/v1/marketing/calendar", tags=["calendar"])


class CalendarEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    channel: str
    title: str
    scheduled_at: datetime
    reference_id: uuid.UUID
    reference_type: str


@router.get("", response_model=list[CalendarEntryOut])
async def list_calendar(channel: str | None = None, start: datetime | None = None, end: datetime | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The single view spanning every channel — populated
    autonomously by scheduling a send or a social post, never by a
    manual entry here."""
    return await calendar_engine.list_calendar(db, tenant_id, channel, start, end)


@router.get("/check-conflict", response_model=list[CalendarEntryOut])
async def check_spacing_conflict(channel: str, proposed_at: datetime, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await calendar_engine.check_spacing_conflict(db, tenant_id, channel, proposed_at)
