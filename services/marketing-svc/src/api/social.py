import uuid
from typing import Literal
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import social_engine

router = APIRouter(prefix="/api/v1/marketing/social", tags=["social"])


class AccountConnect(BaseModel):
    platform: str
    handle: str
    access_token_ref: str = ""


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    platform: str
    handle: str
    connected: bool


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def connect_account(body: AccountConnect, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await social_engine.connect_account(db, tenant_id, body.platform, body.handle, body.access_token_ref)


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await social_engine.list_accounts(db, tenant_id)


@router.post("/accounts/{account_id}/disconnect", response_model=AccountOut)
async def disconnect_account(account_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    account = await social_engine.disconnect_account(db, tenant_id, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Social account not found")
    return account


class PostSchedule(BaseModel):
    social_account_id: uuid.UUID
    content_draft_id: uuid.UUID
    scheduled_at: datetime
    media_asset_id: uuid.UUID | None = None
    recurrence: Literal["none", "daily", "weekly", "3x_week"] = "none"


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    social_account_id: uuid.UUID
    content_draft_id: uuid.UUID
    scheduled_at: datetime
    status: str
    published_at: datetime | None
    platform_post_id: str | None
    delivery_mode: str
    error_detail: str
    media_asset_id: uuid.UUID | None
    recurrence: str


@router.post("/posts", response_model=PostOut, status_code=201)
async def schedule_post(body: PostSchedule, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await social_engine.schedule_post(db, tenant_id, body.social_account_id, body.content_draft_id, body.scheduled_at, body.media_asset_id, body.recurrence)
    except social_engine.DraftNotApprovedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/posts", response_model=list[PostOut])
async def list_posts(status: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await social_engine.list_posts(db, tenant_id, status)


class PublishRequest(BaseModel):
    platform_post_id: str | None = None


@router.post("/posts/{post_id}/publish", response_model=PostOut)
async def publish_post(post_id: uuid.UUID, body: PublishRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous — publishing a scheduled, approved post
    requires no further click."""
    try:
        return await social_engine.publish_post(db, tenant_id, post_id, body.platform_post_id)
    except social_engine.AlreadyPublishedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
