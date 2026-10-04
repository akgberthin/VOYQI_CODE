from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CapitalMetricDomain, CapitalRollupSnapshot


async def get_cross_funding_source_summary(
    db: AsyncSession, tenant_id: str, metric_domain: CapitalMetricDomain
) -> dict:
    result = await db.execute(
        select(CapitalRollupSnapshot)
        .where(
            CapitalRollupSnapshot.tenant_id == tenant_id, CapitalRollupSnapshot.metric_domain == metric_domain
        )
        .order_by(CapitalRollupSnapshot.period_start.desc())
    )
    snapshots = result.scalars().all()

    latest_by_source: dict[str, CapitalRollupSnapshot] = {}
    for snap in snapshots:
        latest_by_source.setdefault(snap.funding_source_key, snap)

    totals: dict[str, float] = defaultdict(float)
    by_source: dict[str, dict] = {}
    for key, snap in latest_by_source.items():
        by_source[key] = {
            "period_start": snap.period_start.isoformat(),
            "period_end": snap.period_end.isoformat(),
            "metrics": snap.metrics,
        }
        for metric_key, value in snap.metrics.items():
            if isinstance(value, (int, float)):
                totals[metric_key] += value

    return {
        "metric_domain": metric_domain.value,
        "funding_source_count": len(by_source),
        "totals": dict(totals),
        "by_funding_source": by_source,
    }


async def get_trend(
    db: AsyncSession,
    tenant_id: str,
    funding_source_key: str,
    metric_domain: CapitalMetricDomain,
    limit: int = 12,
) -> list[dict]:
    result = await db.execute(
        select(CapitalRollupSnapshot)
        .where(
            CapitalRollupSnapshot.tenant_id == tenant_id,
            CapitalRollupSnapshot.funding_source_key == funding_source_key,
            CapitalRollupSnapshot.metric_domain == metric_domain,
        )
        .order_by(CapitalRollupSnapshot.period_start.asc())
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
