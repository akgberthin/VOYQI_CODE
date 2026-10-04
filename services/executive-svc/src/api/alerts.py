import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import AlertStatus, KPIAlert, MetricDomain

router = APIRouter(prefix="/api/v1/executive/alerts", tags=["alerts"])


class AlertOut(BaseModel):
    id: str
    subsidiary_key: str
    metric_domain: MetricDomain
    metric_key: str
    observed_value: float
    threshold_value: float
    status: AlertStatus
    triggered_at: datetime
    resolved_at: datetime | None


def _to_out(a: KPIAlert) -> AlertOut:
    return AlertOut(
        id=str(a.id),
        subsidiary_key=a.subsidiary_key,
        metric_domain=a.metric_domain,
        metric_key=a.metric_key,
        observed_value=a.observed_value,
        threshold_value=a.threshold_value,
        status=a.status,
        triggered_at=a.triggered_at,
        resolved_at=a.resolved_at,
    )


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    status: AlertStatus | None = None,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[AlertOut]:
    stmt = select(KPIAlert).where(KPIAlert.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(KPIAlert.status == status)
    stmt = stmt.order_by(KPIAlert.triggered_at.desc())
    result = await db.execute(stmt)
    return [_to_out(a) for a in result.scalars().all()]


@router.post("/{alert_id}/resolve", response_model=AlertOut)
async def resolve_alert(
    alert_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> AlertOut:
    result = await db.execute(
        select(KPIAlert).where(KPIAlert.tenant_id == tenant_id, KPIAlert.id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(alert)
    return _to_out(alert)
