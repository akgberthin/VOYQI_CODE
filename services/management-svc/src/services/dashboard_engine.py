from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import OpsMetricDomain, OpsRollupSnapshot


async def get_cross_cost_center_summary(
    db: AsyncSession, tenant_id: str, metric_domain: OpsMetricDomain
) -> dict:
    """Sums numeric metrics across each cost center's latest snapshot
    for this domain — each cost center contributes at most one snapshot
    per rollup, so this is a straight sum, not a statistical estimate."""
    result = await db.execute(
        select(OpsRollupSnapshot)
        .where(OpsRollupSnapshot.tenant_id == tenant_id, OpsRollupSnapshot.metric_domain == metric_domain)
        .order_by(OpsRollupSnapshot.period_start.desc())
    )
    snapshots = result.scalars().all()

    latest_by_cost_center: dict[str, OpsRollupSnapshot] = {}
    for snap in snapshots:
        latest_by_cost_center.setdefault(snap.cost_center_key, snap)

    totals: dict[str, float] = defaultdict(float)
    by_cost_center: dict[str, dict] = {}
    for key, snap in latest_by_cost_center.items():
        by_cost_center[key] = {
            "period_start": snap.period_start.isoformat(),
            "period_end": snap.period_end.isoformat(),
            "metrics": snap.metrics,
        }
        for metric_key, value in snap.metrics.items():
            if isinstance(value, (int, float)):
                totals[metric_key] += value

    return {
        "metric_domain": metric_domain.value,
        "cost_center_count": len(by_cost_center),
        "totals": dict(totals),
        "by_cost_center": by_cost_center,
    }


async def get_trend(
    db: AsyncSession,
    tenant_id: str,
    cost_center_key: str,
    metric_domain: OpsMetricDomain,
    limit: int = 12,
) -> list[dict]:
    result = await db.execute(
        select(OpsRollupSnapshot)
        .where(
            OpsRollupSnapshot.tenant_id == tenant_id,
            OpsRollupSnapshot.cost_center_key == cost_center_key,
            OpsRollupSnapshot.metric_domain == metric_domain,
        )
        .order_by(OpsRollupSnapshot.period_start.asc())
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
