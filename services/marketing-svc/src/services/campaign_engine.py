"""Campaign engine. Fully autonomous CRUD — creating, pausing, and
resuming a campaign is not an access-control decision."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Campaign, CampaignStatus


async def create_campaign(db: AsyncSession, tenant_id: str, name: str, channel: str, objective: str, created_by: str, audience_segment_id: uuid.UUID | None = None) -> Campaign:
    campaign = Campaign(tenant_id=tenant_id, name=name, channel=channel, objective=objective, created_by=created_by, audience_segment_id=audience_segment_id)
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def get_campaign(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID) -> Campaign | None:
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_campaigns(db: AsyncSession, tenant_id: str, channel: str | None = None) -> list[Campaign]:
    query = select(Campaign).where(Campaign.tenant_id == tenant_id)
    if channel:
        query = query.where(Campaign.channel == channel)
    result = await db.execute(query)
    return list(result.scalars().all())


async def set_campaign_status(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID, status: str) -> Campaign | None:
    campaign = await get_campaign(db, tenant_id, campaign_id)
    if campaign is None:
        return None
    campaign.status = status
    await db.commit()
    await db.refresh(campaign)
    return campaign
