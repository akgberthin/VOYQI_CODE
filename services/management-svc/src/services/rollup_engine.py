from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.kafka_events import publish_event
from src.models import OpsMetricDomain, OpsRollupSnapshot
from src.services.threshold_engine import evaluate_thresholds


async def ingest_snapshot(
    db: AsyncSession,
    tenant_id: str,
    cost_center_key: str,
    metric_domain: OpsMetricDomain,
    period_start: datetime,
    period_end: datetime,
    metrics: dict,
    source_service: str,
) -> OpsRollupSnapshot:
    snapshot = OpsRollupSnapshot(
        tenant_id=tenant_id,
        cost_center_key=cost_center_key,
        metric_domain=metric_domain,
        period_start=period_start,
        period_end=period_end,
        metrics=metrics,
        source_service=source_service,
    )
    db.add(snapshot)
    await db.flush()

    alerts = await evaluate_thresholds(db, tenant_id, cost_center_key, metric_domain, metrics)

    await db.commit()
    await db.refresh(snapshot)

    await publish_event(
        "management.snapshot.ingested",
        {
            "tenant_id": tenant_id,
            "cost_center_key": cost_center_key,
            "metric_domain": metric_domain.value,
            "snapshot_id": str(snapshot.id),
        },
    )
    for alert in alerts:
        await publish_event(
            "management.kpi_alert.triggered",
            {
                "tenant_id": tenant_id,
                "cost_center_key": cost_center_key,
                "metric_domain": metric_domain.value,
                "metric_key": alert.metric_key,
                "alert_id": str(alert.id),
            },
        )

    return snapshot


async def list_snapshots(
    db: AsyncSession,
    tenant_id: str,
    cost_center_key: str | None = None,
    metric_domain: OpsMetricDomain | None = None,
    limit: int = 50,
) -> list[OpsRollupSnapshot]:
    stmt = select(OpsRollupSnapshot).where(OpsRollupSnapshot.tenant_id == tenant_id)
    if cost_center_key:
        stmt = stmt.where(OpsRollupSnapshot.cost_center_key == cost_center_key)
    if metric_domain:
        stmt = stmt.where(OpsRollupSnapshot.metric_domain == metric_domain)
    stmt = stmt.order_by(OpsRollupSnapshot.period_start.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
