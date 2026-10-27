import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import ai_drafting, autopilot, media_engine

router = APIRouter(prefix="/api/v1/marketing/autopilot", tags=["autopilot"])


class ConfigIn(BaseModel):
    enabled: bool | None = None
    mode: str | None = None
    account_ids: list[str] | None = None
    posts_per_week: int | None = None
    post_hour_utc: int | None = None
    themes: str | None = None
    mobility_class: str | None = None
    tone: str | None = None
    include_images: bool | None = None


class ConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    enabled: bool
    mode: str
    account_ids: list
    posts_per_week: int
    post_hour_utc: int
    themes: str
    mobility_class: str
    tone: str
    include_images: bool
    last_planned_at: datetime | None
    updated_by: str


class SlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    social_account_id: uuid.UUID
    planned_at: datetime
    theme: str
    content_draft_id: uuid.UUID | None
    media_asset_id: uuid.UUID | None
    social_post_id: uuid.UUID | None
    status: str
    decided_by: str
    image_url: str | None = None


def _slot_out(slot) -> SlotOut:
    out = SlotOut.model_validate(slot)
    out.image_url = media_engine.public_url(slot.media_asset_id)
    return out


@router.get("", response_model=ConfigOut)
async def get_config(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await autopilot.get_config(db, tenant_id)


@router.put("", response_model=ConfigOut)
async def save_config(body: ConfigIn, tenant_id: str = Depends(get_tenant_id), x_user_id: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    try:
        return await autopilot.save_config(db, tenant_id, body.model_dump(exclude_none=True), x_user_id or "marketing-admin")
    except autopilot.AutopilotError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/plan-now")
async def plan_now(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Plan the coming week now instead of waiting for the next pass."""
    try:
        return {"planned": await autopilot.plan(db, tenant_id)}
    except ai_drafting.AIDraftUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/slots", response_model=list[SlotOut])
async def list_slots(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return [_slot_out(s) for s in await autopilot.list_slots(db, tenant_id)]


@router.post("/slots/{slot_id}/approve", response_model=SlotOut)
async def approve(slot_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), x_user_id: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    try:
        return _slot_out(await autopilot.approve(db, tenant_id, slot_id, x_user_id or "marketing-admin"))
    except autopilot.AutopilotError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/slots/{slot_id}/reject", response_model=SlotOut)
async def reject(slot_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), x_user_id: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    try:
        return _slot_out(await autopilot.reject(db, tenant_id, slot_id, x_user_id or "marketing-admin"))
    except autopilot.AutopilotError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
