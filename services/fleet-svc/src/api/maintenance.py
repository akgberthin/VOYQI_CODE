import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import MaintenanceAlertSeverity, WorkOrderStatus
from src.services import maintenance_engine

router = APIRouter(prefix="/api/v1/fleet", tags=["maintenance"])


class AlertEvaluationRequest(BaseModel):
    vehicle_id: uuid.UUID
    miles_since_service: int
    dtc_codes: list[str] = []


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    severity: MaintenanceAlertSeverity
    reason: str
    dtc_codes: list[str]
    acknowledged: bool


class WorkOrderCreate(BaseModel):
    vehicle_id: uuid.UUID
    vendor: str
    created_by: str
    alert_id: uuid.UUID | None = None
    description: str = ""


class WorkOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    alert_id: uuid.UUID | None
    vendor: str
    status: WorkOrderStatus
    downtime_start: datetime
    downtime_end: datetime | None
    cost_usd: float


@router.post("/maintenance-alerts/evaluate")
async def evaluate_alert(
    body: AlertEvaluationRequest, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    alert = await maintenance_engine.evaluate_maintenance_need(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, miles_since_service=body.miles_since_service, dtc_codes=body.dtc_codes,
    )
    if alert is None:
        return {"alert_created": False}
    # AI-first, but not on every single alert — re-triage automatically
    # once enough open alerts have accumulated to actually need
    # prioritizing, the same "meaningful accumulation" principle as
    # safety scoring's every-5th-event reassessment.
    open_alerts = await maintenance_engine.list_open_unacknowledged_alerts(db, tenant_id)
    if len(open_alerts) >= maintenance_engine.TRIAGE_ALERT_COUNT_THRESHOLD:
        alert_summaries = [{"alert_id": str(a.id), "vehicle_id": str(a.vehicle_id), "severity": a.severity.value, "reason": a.reason, "dtc_codes": a.dtc_codes} for a in open_alerts]
        background_tasks.add_task(
            publish_event,
            "fleet.maintenance",
            "flt.maintenance_triage_requested",
            tenant_id,
            {"alerts": alert_summaries},
        )
    return {"alert_created": True, "alert": AlertOut.model_validate(alert)}


@router.post("/maintenance-alerts/triage-now")
async def triage_now(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation alert
    accumulation already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    open_alerts = await maintenance_engine.list_open_unacknowledged_alerts(db, tenant_id)
    alert_summaries = [{"alert_id": str(a.id), "vehicle_id": str(a.vehicle_id), "severity": a.severity.value, "reason": a.reason, "dtc_codes": a.dtc_codes} for a in open_alerts]
    response = ai_orchestration_tools.request_maintenance_triage(tenant_id, alert_summaries)
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response


class TriageAssessmentCreate(BaseModel):
    alert_ids: list[str]
    priority_order: list[str]
    reasoning: str = ""
    agent_key: str = "fleet_intelligence_agent"


@router.post("/maintenance-triage-assessments")
async def create_triage_assessment(body: TriageAssessmentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The agent's FLT-004 tool call — recording the triage order and
    autonomously opening a work order for the top-priority alert
    happen in the same call, no human dispatch step."""
    assessment = await maintenance_engine.record_maintenance_triage(db, tenant_id, body.alert_ids, body.priority_order, body.reasoning)
    work_order = await maintenance_engine.auto_dispatch_top_priority_work_order(db, tenant_id, body.priority_order, body.agent_key)
    return {
        "id": str(assessment.id), "priority_order": assessment.priority_order,
        "auto_dispatched_work_order_id": str(work_order.id) if work_order else None,
    }


@router.get("/maintenance-triage-assessments/latest")
async def get_latest_triage_assessment(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    assessment = await maintenance_engine.get_latest_maintenance_triage(db, tenant_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="No triage assessment on file")
    return {"id": str(assessment.id), "alert_ids": assessment.alert_ids, "priority_order": assessment.priority_order, "reasoning": assessment.reasoning}


@router.post("/work-orders", response_model=WorkOrderOut, status_code=201)
async def create_work_order(body: WorkOrderCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await maintenance_engine.create_work_order(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, vendor=body.vendor, created_by=body.created_by,
        alert_id=body.alert_id, description=body.description,
    )


@router.post("/work-orders/{work_order_id}/complete", response_model=WorkOrderOut)
async def complete_work_order(work_order_id: uuid.UUID, cost_usd: float = 0.0, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    order = await maintenance_engine.complete_work_order(db, tenant_id, work_order_id, cost_usd)
    if order is None:
        raise HTTPException(status_code=409, detail="Work order not found or already completed")
    return order
