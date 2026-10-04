from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import MetricDomain, RollupSnapshot


async def get_cross_subsidiary_summary(
    db: AsyncSession, tenant_id: str, metric_domain: MetricDomain
) -> dict:
    """Sums numeric metrics across each subsidiary's latest snapshot for
    this domain — each subsidiary contributes at most one snapshot per
    rollup, so this is a straight sum, not a statistical estimate."""
    result = await db.execute(
        select(RollupSnapshot)
        .where(RollupSnapshot.tenant_id == tenant_id, RollupSnapshot.metric_domain == metric_domain)
        .order_by(RollupSnapshot.period_start.desc())
    )
    snapshots = result.scalars().all()

    latest_by_subsidiary: dict[str, RollupSnapshot] = {}
    for snap in snapshots:
        latest_by_subsidiary.setdefault(snap.subsidiary_key, snap)

    totals: dict[str, float] = defaultdict(float)
    per_subsidiary: dict[str, dict] = {}
    for key, snap in latest_by_subsidiary.items():
        per_subsidiary[key] = {
            "period_start": snap.period_start.isoformat(),
            "period_end": snap.period_end.isoformat(),
            "metrics": snap.metrics,
        }
        for metric_key, value in snap.metrics.items():
            if isinstance(value, (int, float)):
                totals[metric_key] += value

    return {
        "metric_domain": metric_domain.value,
        "subsidiary_count": len(per_subsidiary),
        "totals": dict(totals),
        "by_subsidiary": per_subsidiary,
    }


async def get_trend(
    db: AsyncSession,
    tenant_id: str,
    subsidiary_key: str,
    metric_domain: MetricDomain,
    limit: int = 12,
) -> list[dict]:
    result = await db.execute(
        select(RollupSnapshot)
        .where(
            RollupSnapshot.tenant_id == tenant_id,
            RollupSnapshot.subsidiary_key == subsidiary_key,
            RollupSnapshot.metric_domain == metric_domain,
        )
        .order_by(RollupSnapshot.period_start.asc())
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
