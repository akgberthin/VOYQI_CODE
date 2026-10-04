import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import AlertStatus, OpsKPIAlert, OpsMetricDomain

router = APIRouter(prefix="/api/v1/management/alerts", tags=["alerts"])


class AlertOut(BaseModel):
    id: str
    cost_center_key: str
    metric_domain: OpsMetricDomain
    metric_key: str
    observed_value: float
    threshold_value: float
    status: AlertStatus
    triggered_at: datetime
    resolved_at: datetime | None


def _to_out(a: OpsKPIAlert) -> AlertOut:
    return AlertOut(
        id=str(a.id),
        cost_center_key=a.cost_center_key,
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
    stmt = select(OpsKPIAlert).where(OpsKPIAlert.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(OpsKPIAlert.status == status)
    stmt = stmt.order_by(OpsKPIAlert.triggered_at.desc())
    result = await db.execute(stmt)
    return [_to_out(a) for a in result.scalars().all()]


@router.post("/{alert_id}/resolve", response_model=AlertOut)
async def resolve_alert(
    alert_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> AlertOut:
    result = await db.execute(
        select(OpsKPIAlert).where(OpsKPIAlert.tenant_id == tenant_id, OpsKPIAlert.id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(alert)
    return _to_out(alert)
