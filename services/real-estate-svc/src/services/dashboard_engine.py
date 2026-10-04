from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import PropertyRollupSnapshot, RealEstateMetricDomain


async def get_cross_property_summary(
    db: AsyncSession, tenant_id: str, metric_domain: RealEstateMetricDomain
) -> dict:
    result = await db.execute(
        select(PropertyRollupSnapshot)
        .where(
            PropertyRollupSnapshot.tenant_id == tenant_id, PropertyRollupSnapshot.metric_domain == metric_domain
        )
        .order_by(PropertyRollupSnapshot.period_start.desc())
    )
    snapshots = result.scalars().all()

    latest_by_property: dict[str, PropertyRollupSnapshot] = {}
    for snap in snapshots:
        latest_by_property.setdefault(snap.property_key, snap)

    totals: dict[str, float] = defaultdict(float)
    by_property: dict[str, dict] = {}
    for key, snap in latest_by_property.items():
        by_property[key] = {
            "period_start": snap.period_start.isoformat(),
            "period_end": snap.period_end.isoformat(),
            "metrics": snap.metrics,
        }
        for metric_key, value in snap.metrics.items():
            if isinstance(value, (int, float)):
                totals[metric_key] += value

    return {
        "metric_domain": metric_domain.value,
        "property_count": len(by_property),
        "totals": dict(totals),
        "by_property": by_property,
    }


async def get_trend(
    db: AsyncSession,
    tenant_id: str,
    property_key: str,
    metric_domain: RealEstateMetricDomain,
    limit: int = 12,
) -> list[dict]:
    result = await db.execute(
        select(PropertyRollupSnapshot)
        .where(
            PropertyRollupSnapshot.tenant_id == tenant_id,
            PropertyRollupSnapshot.property_key == property_key,
            PropertyRollupSnapshot.metric_domain == metric_domain,
        )
        .order_by(PropertyRollupSnapshot.period_start.asc())
        .limit(limit)
    )
    snapshots = result.scalars().all()
    return [
        {
            "period_start": s.period_start.isoformat(),
            "period_end": s.period_end.isoformat(),
            "metrics": s.metrics,
        }
        for s in snapshots
    ]
