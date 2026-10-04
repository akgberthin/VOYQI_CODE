from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import OpsMetricDomain
from src.services import dashboard_engine

router = APIRouter(prefix="/api/v1/management/dashboard", tags=["dashboard"])


@router.get("/summary")
async def summary(
    metric_domain: OpsMetricDomain,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await dashboard_engine.get_cross_cost_center_summary(db, tenant_id, metric_domain)


@router.get("/trend")
async def trend(
    cost_center_key: str,
    metric_domain: OpsMetricDomain,
    limit: int = 12,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await dashboard_engine.get_trend(db, tenant_id, cost_center_key, metric_domain, limit)
