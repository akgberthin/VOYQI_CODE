from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.kafka_events import publish_event
from src.models import CapitalMetricDomain, CapitalRollupSnapshot
from src.services.threshold_engine import evaluate_thresholds


async def ingest_snapshot(
    db: AsyncSession,
    tenant_id: str,
    funding_source_key: str,
    metric_domain: CapitalMetricDomain,
    period_start: datetime,
    period_end: datetime,
    metrics: dict,
    source_service: str,
) -> CapitalRollupSnapshot:
    snapshot = CapitalRollupSnapshot(
        tenant_id=tenant_id,
        funding_source_key=funding_source_key,
        metric_domain=metric_domain,
        period_start=period_start,
        period_end=period_end,
        metrics=metrics,
        source_service=source_service,
    )
    db.add(snapshot)
    await db.flush()

    alerts = await evaluate_thresholds(db, tenant_id, funding_source_key, metric_domain, metrics)

    await db.commit()
    await db.refresh(snapshot)

    await publish_event(
        "capital_finance.snapshot.ingested",
        {
            "tenant_id": tenant_id,
            "funding_source_key": funding_source_key,
            "metric_domain": metric_domain.value,
            "snapshot_id": str(snapshot.id),
        },
    )
    for alert in alerts:
        await publish_event(
            "capital_finance.kpi_alert.triggered",
            {
                "tenant_id": tenant_id,
                "funding_source_key": funding_source_key,
                "metric_domain": metric_domain.value,
                "metric_key": alert.metric_key,
                "alert_id": str(alert.id),
            },
        )

    return snapshot


async def list_snapshots(
    db: AsyncSession,
    tenant_id: str,
    funding_source_key: str | None = None,
    metric_domain: CapitalMetricDomain | None = None,
    limit: int = 50,
) -> list[CapitalRollupSnapshot]:
    stmt = select(CapitalRollupSnapshot).where(CapitalRollupSnapshot.tenant_id == tenant_id)
    if funding_source_key:
        stmt = stmt.where(CapitalRollupSnapshot.funding_source_key == funding_source_key)
    if metric_domain:
        stmt = stmt.where(CapitalRollupSnapshot.metric_domain == metric_domain)
    stmt = stmt.order_by(CapitalRollupSnapshot.period_start.desc()).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
