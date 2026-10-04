import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import RealEstateMetricDomain, ThresholdComparison
from src.services import threshold_engine

router = APIRouter(prefix="/api/v1/real-estate/thresholds", tags=["thresholds"])


class ThresholdIn(BaseModel):
    metric_domain: RealEstateMetricDomain
    metric_key: str
    comparison: ThresholdComparison
    threshold_value: float


class ThresholdOut(BaseModel):
    id: str
    metric_domain: RealEstateMetricDomain
    metric_key: str
    comparison: ThresholdComparison
    threshold_value: float
    is_active: bool


def _to_out(t) -> ThresholdOut:
    return ThresholdOut(
        id=str(t.id),
        metric_domain=t.metric_domain,
        metric_key=t.metric_key,
        comparison=t.comparison,
        threshold_value=t.threshold_value,
        is_active=t.is_active,
    )


@router.post("", response_model=ThresholdOut, status_code=201)
async def create_threshold(
    payload: ThresholdIn,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ThresholdOut:
    threshold = await threshold_engine.create_threshold(
        db, tenant_id, payload.metric_domain, payload.metric_key, payload.comparison, payload.threshold_value
    )
    return _to_out(threshold)


@router.get("", response_model=list[ThresholdOut])
async def list_thresholds(
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> list[ThresholdOut]:
    thresholds = await threshold_engine.list_thresholds(db, tenant_id)
    return [_to_out(t) for t in thresholds]


@router.delete("/{threshold_id}", response_model=ThresholdOut)
async def deactivate_threshold(
    threshold_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ThresholdOut:
    threshold = await threshold_engine.deactivate_threshold(db, tenant_id, threshold_id)
    if threshold is None:
        raise HTTPException(status_code=404, detail="Threshold not found")
    return _to_out(threshold)
