from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import CapitalMetricDomain
from src.services import rollup_engine

router = APIRouter(prefix="/api/v1/capital-finance/rollups", tags=["rollups"])


class SnapshotIn(BaseModel):
    funding_source_key: str
    metric_domain: CapitalMetricDomain
    period_start: datetime
    period_end: datetime
    metrics: dict
    source_service: str


class SnapshotOut(BaseModel):
    id: str
    funding_source_key: str
    metric_domain: CapitalMetricDomain
    period_start: datetime
    period_end: datetime
    metrics: dict
    source_service: str


@router.post("", response_model=SnapshotOut, status_code=201)
async def ingest_snapshot(
    payload: SnapshotIn,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> SnapshotOut:
    snapshot = await rollup_engine.ingest_snapshot(
        db,
        tenant_id,
        payload.funding_source_key,
        payload.metric_domain,
        payload.period_start,
        payload.period_end,
        payload.metrics,
        payload.source_service,
    )
    return SnapshotOut(
        id=str(snapshot.id),
        funding_source_key=snapshot.funding_source_key,
        metric_domain=snapshot.metric_domain,
        period_start=snapshot.period_start,
        period_end=snapshot.period_end,
        metrics=snapshot.metrics,
        source_service=snapshot.source_service,
    )


@router.get("", response_model=list[SnapshotOut])
async def list_snapshots(
    funding_source_key: str | None = None,
    metric_domain: CapitalMetricDomain | None = None,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[SnapshotOut]:
    snapshots = await rollup_engine.list_snapshots(db, tenant_id, funding_source_key, metric_domain)
    return [
        SnapshotOut(
            id=str(s.id),
            funding_source_key=s.funding_source_key,
            metric_domain=s.metric_domain,
            period_start=s.period_start,
            period_end=s.period_end,
            metrics=s.metrics,
            source_service=s.source_service,
        )
        for s in snapshots
    ]
