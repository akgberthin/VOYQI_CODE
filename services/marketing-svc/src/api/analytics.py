import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import analytics_engine

router = APIRouter(prefix="/api/v1/marketing/analytics", tags=["analytics"])


class SnapshotCreate(BaseModel):
    campaign_id: uuid.UUID
    channel: str
    sent_count: int = 0
    open_count: int = 0
    click_count: int = 0
    conversion_count: int = 0
    revenue_usd: float = 0.0


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    campaign_id: uuid.UUID
    channel: str
    sent_count: int
    open_count: int
    click_count: int
    conversion_count: int
    revenue_usd: float
    snapshot_at: datetime | None = None


@router.post("/snapshots", response_model=SnapshotOut, status_code=201)
async def record_snapshot(body: SnapshotCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await analytics_engine.record_snapshot(db, tenant_id, body.campaign_id, body.channel, body.sent_count, body.open_count, body.click_count, body.conversion_count, body.revenue_usd)


@router.get("/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(campaign_id: uuid.UUID | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await analytics_engine.list_snapshots(db, tenant_id, campaign_id)


@router.get("/campaigns/{campaign_id}/summary")
async def get_campaign_summary(campaign_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Real transaction arithmetic — no AI reasoning belongs here."""
    return await analytics_engine.get_campaign_summary(db, tenant_id, campaign_id)
