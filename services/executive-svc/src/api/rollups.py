from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import MetricDomain
from src.services import rollup_engine

router = APIRouter(prefix="/api/v1/executive/rollups", tags=["rollups"])


class SnapshotIn(BaseModel):
    subsidiary_key: str
    metric_domain: MetricDomain
    period_start: datetime
    period_end: datetime
    metrics: dict
    source_service: str


class SnapshotOut(BaseModel):
    id: str
    subsidiary_key: str
    metric_domain: MetricDomain
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
        payload.subsidiary_key,
        payload.metric_domain,
        payload.period_start,
        payload.period_end,
        payload.metrics,
        payload.source_service,
    )
    return SnapshotOut(
        id=str(snapshot.id),
        subsidiary_key=snapshot.subsidiary_key,
        metric_domain=snapshot.metric_domain,
        period_start=snapshot.period_start,
        period_end=snapshot.period_end,
        metrics=snapshot.metrics,
        source_service=snapshot.source_service,
    )


@router.get("", response_model=list[SnapshotOut])
async def list_snapshots(
    subsidiary_key: str | None = None,
    metric_domain: MetricDomain | None = None,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[SnapshotOut]:
    snapshots = await rollup_engine.list_snapshots(db, tenant_id, subsidiary_key, metric_domain)
    return [
        SnapshotOut(
            id=str(s.id),
            subsidiary_key=s.subsidiary_key,
            metric_domain=s.metric_domain,
            period_start=s.period_start,
            period_end=s.period_end,
            metrics=s.metrics,
            source_service=s.source_service,
        )
        for s in snapshots
    ]
