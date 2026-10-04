import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import campaign_engine

router = APIRouter(prefix="/api/v1/marketing/campaigns", tags=["campaigns"])


class CampaignCreate(BaseModel):
    name: str
    channel: str
    objective: str = ""
    created_by: str = "marketing-admin"
    audience_segment_id: uuid.UUID | None = None


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    channel: str
    objective: str
    status: str
    audience_segment_id: uuid.UUID | None
    created_by: str


@router.post("", response_model=CampaignOut, status_code=201)
async def create_campaign(body: CampaignCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await campaign_engine.create_campaign(db, tenant_id, body.name, body.channel, body.objective, body.created_by, body.audience_segment_id)


@router.get("", response_model=list[CampaignOut])
async def list_campaigns(channel: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await campaign_engine.list_campaigns(db, tenant_id, channel)


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    campaign = await campaign_engine.get_campaign(db, tenant_id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


class StatusUpdate(BaseModel):
    status: str


@router.patch("/{campaign_id}/status", response_model=CampaignOut)
async def set_campaign_status(campaign_id: uuid.UUID, body: StatusUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    campaign = await campaign_engine.set_campaign_status(db, tenant_id, campaign_id, body.status)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign
