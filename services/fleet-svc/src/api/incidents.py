import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import (
    IncidentStatus,
    IncidentType,
    LiabilityAssessment,
    WorkOrderStatus,
)
from src.services import incident_engine

router = APIRouter(prefix="/api/v1/fleet", tags=["incidents"])


class IncidentReport(BaseModel):
    vehicle_id: uuid.UUID
    incident_type: IncidentType
    description: str
    reported_by: str
    photo_refs: list[str] = []


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    incident_type: IncidentType
    description: str
    liability_assessment: LiabilityAssessment
    status: IncidentStatus
    insurance_carrier: str
    claim_number: str
    created_at: datetime


class LiabilityRequest(BaseModel):
    liability: LiabilityAssessment
    assessed_by: str


class InsuranceHandoff(BaseModel):
    carrier: str
    claim_number: str


class RepairOrderCreate(BaseModel):
    incident_id: uuid.UUID
    vendor: str
    estimated_cost_usd: float = 0.0


class RepairOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    vendor: str
    status: WorkOrderStatus
    estimated_cost_usd: float


class TotalLossRequest(BaseModel):
    declared_by: str
    reason: str


@router.post("/incidents", response_model=IncidentOut, status_code=201)
async def report_incident(
    body: IncidentReport, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    incident = await incident_engine.report_incident(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, incident_type=body.incident_type,
        description=body.description, reported_by=body.reported_by, photo_refs=body.photo_refs,
    )
    # AI-first: every incident gets a severity triage automatically —
    # this never touches liability_assessment, which remains
    # exclusively set by assess_liability below, a human-only action.
    background_tasks.add_task(
        publish_event,
        "fleet.incidents",
        "flt.incident_severity_requested",
        tenant_id,
        {"incident_id": str(incident.id), "incident_type": incident.incident_type.value, "description": incident.description},
    )
    return incident


@router.post("/incidents/{incident_id}/assess-severity-now")
async def assess_severity_now(incident_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation incident
    reporting already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    incident = await incident_engine.get_incident(db, tenant_id, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    response = ai_orchestration_tools.request_incident_severity(tenant_id, str(incident_id), incident.incident_type.value, incident.description)
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response


class SeverityAssessmentCreate(BaseModel):
    severity: str
    summary: str = ""
    reasoning: str = ""
    agent_key: str = "fleet_intelligence_agent"


@router.post("/incidents/{incident_id}/severity-assessments")
async def create_severity_assessment(incident_id: uuid.UUID, body: SeverityAssessmentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The agent's FLT-005 tool call — recording the severity read and
    autonomously opening a repair order for SEVERE incidents happen in
    the same call. liability_assessment is never touched here; that
    field has its own, always-human endpoint (assess_liability)."""
    from src.models import IncidentSeverityLevel

    level = IncidentSeverityLevel(body.severity)
    assessment = await incident_engine.record_incident_severity(db, tenant_id, incident_id, level, body.summary, body.reasoning)
    repair_order = await incident_engine.auto_execute_incident_severity(db, tenant_id, incident_id, level, body.agent_key)
    return {
        "id": str(assessment.id), "severity": assessment.severity.value,
        "auto_repair_order_id": str(repair_order.id) if repair_order else None,
    }


@router.get("/incidents/{incident_id}/severity")
async def get_incident_severity(incident_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    assessment = await incident_engine.get_incident_severity(db, tenant_id, incident_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="No severity assessment on file")
    return {"id": str(assessment.id), "severity": assessment.severity.value, "summary": assessment.summary, "reasoning": assessment.reasoning}


@router.post("/incidents/{incident_id}/assess-liability", response_model=IncidentOut)
async def assess_liability(incident_id: uuid.UUID, body: LiabilityRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    incident = await incident_engine.assess_liability(db, tenant_id, incident_id, body.liability, body.assessed_by)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.post("/incidents/{incident_id}/insurance-handoff", response_model=IncidentOut)
async def handoff_to_insurance(incident_id: uuid.UUID, body: InsuranceHandoff, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    incident = await incident_engine.handoff_to_insurance(db, tenant_id, incident_id, body.carrier, body.claim_number)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.post("/repair-orders", response_model=RepairOrderOut, status_code=201)
async def create_repair_order(body: RepairOrderCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await incident_engine.create_repair_order(db, tenant_id, body.incident_id, body.vendor, body.estimated_cost_usd)


@router.post("/repair-orders/{repair_order_id}/complete", response_model=RepairOrderOut)
async def complete_repair(repair_order_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    order = await incident_engine.complete_repair(db, tenant_id, repair_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Repair order not found")
    return order


@router.post("/incidents/{incident_id}/total-loss", response_model=IncidentOut)
async def declare_total_loss(
    incident_id: uuid.UUID,
    body: TotalLossRequest,
    background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    incident = await incident_engine.declare_total_loss(db, tenant_id, incident_id, body.declared_by, body.reason)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    background_tasks.add_task(
        publish_event,
        "fleet.incidents",
        "incident.total_loss_declared",
        tenant_id,
        {
            "incident_id": str(incident.id), "vehicle_id": str(incident.vehicle_id),
            "declared_by": incident.total_loss_declared_by, "reason": incident.total_loss_reason,
        },
    )
    return incident
