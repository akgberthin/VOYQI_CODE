"""
Analytics engine — real transaction arithmetic, no AI reasoning
belongs on a metrics row, same principle as every other financial/
performance rollup on this platform. Snapshot ingestion is fully
autonomous (no approval needed to record a real count); the aggregate
summary is pure arithmetic over what's already been recorded.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import AnalyticsSnapshot


async def record_snapshot(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID, channel: str, sent_count: int, open_count: int, click_count: int, conversion_count: int, revenue_usd: float) -> AnalyticsSnapshot:
    snapshot = AnalyticsSnapshot(
        tenant_id=tenant_id, campaign_id=campaign_id, channel=channel, sent_count=sent_count,
        open_count=open_count, click_count=click_count, conversion_count=conversion_count, revenue_usd=revenue_usd,
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


async def get_campaign_summary(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID) -> dict:
    result = await db.execute(select(AnalyticsSnapshot).where(AnalyticsSnapshot.tenant_id == tenant_id, AnalyticsSnapshot.campaign_id == campaign_id))
    snapshots = list(result.scalars().all())
    total_sent = sum(s.sent_count for s in snapshots)
    total_open = sum(s.open_count for s in snapshots)
    total_click = sum(s.click_count for s in snapshots)
    total_conversion = sum(s.conversion_count for s in snapshots)
    total_revenue = sum(s.revenue_usd for s in snapshots)
    return {
        "campaign_id": str(campaign_id),
        "total_sent": total_sent,
        "total_open": total_open,
        "total_click": total_click,
        "total_conversion": total_conversion,
        "total_revenue_usd": round(total_revenue, 2),
        "open_rate": round(total_open / total_sent, 4) if total_sent else 0.0,
        "click_rate": round(total_click / total_sent, 4) if total_sent else 0.0,
        "conversion_rate": round(total_conversion / total_sent, 4) if total_sent else 0.0,
    }


async def list_snapshots(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID | None = None) -> list[AnalyticsSnapshot]:
    query = select(AnalyticsSnapshot).where(AnalyticsSnapshot.tenant_id == tenant_id)
    if campaign_id:
        query = query.where(AnalyticsSnapshot.campaign_id == campaign_id)
    result = await db.execute(query)
    return list(result.scalars().all())
