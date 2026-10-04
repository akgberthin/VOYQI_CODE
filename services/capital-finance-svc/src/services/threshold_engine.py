from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CapitalKPIAlert, CapitalKPIThreshold, CapitalMetricDomain, ThresholdComparison


def _breaches(comparison: ThresholdComparison, observed: float, threshold: float) -> bool:
    if comparison == ThresholdComparison.GTE:
        return observed >= threshold
    return observed <= threshold


async def evaluate_thresholds(
    db: AsyncSession,
    tenant_id: str,
    funding_source_key: str,
    metric_domain: CapitalMetricDomain,
    metrics: dict,
) -> list[CapitalKPIAlert]:
    result = await db.execute(
        select(CapitalKPIThreshold).where(
            CapitalKPIThreshold.tenant_id == tenant_id,
            CapitalKPIThreshold.metric_domain == metric_domain,
            CapitalKPIThreshold.is_active.is_(True),
        )
    )
    thresholds = result.scalars().all()

    triggered: list[CapitalKPIAlert] = []
    for threshold in thresholds:
        observed = metrics.get(threshold.metric_key)
        if not isinstance(observed, (int, float)):
            continue
        if _breaches(threshold.comparison, float(observed), threshold.threshold_value):
            alert = CapitalKPIAlert(
                tenant_id=tenant_id,
                funding_source_key=funding_source_key,
                metric_domain=metric_domain,
                metric_key=threshold.metric_key,
                observed_value=float(observed),
                threshold_value=threshold.threshold_value,
                comparison=threshold.comparison,
            )
            db.add(alert)
            triggered.append(alert)

    if triggered:
        await db.flush()
    return triggered


async def create_threshold(
    db: AsyncSession,
    tenant_id: str,
    metric_domain: CapitalMetricDomain,
    metric_key: str,
    comparison: ThresholdComparison,
    threshold_value: float,
) -> CapitalKPIThreshold:
    threshold = CapitalKPIThreshold(
        tenant_id=tenant_id,
        metric_domain=metric_domain,
        metric_key=metric_key,
        comparison=comparison,
        threshold_value=threshold_value,
    )
    db.add(threshold)
    await db.commit()
    await db.refresh(threshold)
    return threshold


async def list_thresholds(db: AsyncSession, tenant_id: str) -> list[CapitalKPIThreshold]:
    result = await db.execute(select(CapitalKPIThreshold).where(CapitalKPIThreshold.tenant_id == tenant_id))
    return list(result.scalars().all())


async def deactivate_threshold(db: AsyncSession, tenant_id: str, threshold_id) -> CapitalKPIThreshold | None:
    result = await db.execute(
        select(CapitalKPIThreshold).where(
            CapitalKPIThreshold.tenant_id == tenant_id, CapitalKPIThreshold.id == threshold_id
        )
    )
    threshold = result.scalar_one_or_none()
    if threshold is None:
        return None
    threshold.is_active = False
    await db.commit()
    await db.refresh(threshold)
    return threshold
