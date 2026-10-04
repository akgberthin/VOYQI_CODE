from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import RealEstateMetricDomain
from src.services import dashboard_engine

router = APIRouter(prefix="/api/v1/real-estate/dashboard", tags=["dashboard"])


@router.get("/summary")
async def summary(
    metric_domain: RealEstateMetricDomain,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await dashboard_engine.get_cross_property_summary(db, tenant_id, metric_domain)


@router.get("/trend")
async def trend(
    property_key: str,
    metric_domain: RealEstateMetricDomain,
    limit: int = 12,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await dashboard_engine.get_trend(db, tenant_id, property_key, metric_domain, limit)
