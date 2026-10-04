import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import (
    IntakeReadinessRecommendation,
    PurchaseOrder,
    PurchaseOrderStatus,
    VehicleClass,
    VehicleStatus,
)
from src.services import inventory_engine
from src.services.inventory_engine import NotProvisionedError

router = APIRouter(prefix="/api/v1/fleet", tags=["inventory"])


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vin: str
    make: str
    model: str
    year: int
    vehicle_class: VehicleClass
    status: VehicleStatus
    current_hub_id: uuid.UUID | None
    odometer_miles: int
    ownership_entity: str
    is_provisioned: bool
    is_class_certified: bool


class POCreate(BaseModel):
    source: str
    vehicle_description: str
    cost_usd: float
    created_by: str


class POOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    vehicle_description: str
    cost_usd: float
    status: PurchaseOrderStatus
    vehicle_id: uuid.UUID | None


class ReceiveRequest(BaseModel):
    vin: str
    make: str
    model: str
    year: int
    vehicle_class: VehicleClass
    ownership_entity: str = ""


class InspectionRequest(BaseModel):
    checklist: dict
    passed: bool
    inspected_by: str
    notes: str = ""


class ProvisionRequest(BaseModel):
    provisioned_by: str
    card_id: str


class CertifyRequest(BaseModel):
    certified_by: str


class StatusUpdate(BaseModel):
    status: VehicleStatus


@router.post("/purchase-orders", response_model=POOut, status_code=201)
async def create_po(body: POCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await inventory_engine.create_purchase_order(
        db, tenant_id=tenant_id, source=body.source, vehicle_description=body.vehicle_description,
        cost_usd=body.cost_usd, created_by=body.created_by,
    )


@router.post("/purchase-orders/{po_id}/receive", response_model=VehicleOut)
async def receive_vehicle(po_id: uuid.UUID, body: ReceiveRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    vehicle = await inventory_engine.receive_vehicle(
        db, tenant_id=tenant_id, po_id=po_id, vin=body.vin, make=body.make, model=body.model,
        year=body.year, vehicle_class=body.vehicle_class, ownership_entity=body.ownership_entity,
    )
    if vehicle is None:
        raise HTTPException(status_code=409, detail="Purchase order not found or not in 'ordered' status")
    return vehicle


@router.post("/purchase-orders/{po_id}/inspect")
async def record_inspection(
    po_id: uuid.UUID, body: InspectionRequest, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    inspection = await inventory_engine.record_inspection(
        db, tenant_id=tenant_id, po_id=po_id, checklist=body.checklist, passed=body.passed,
        inspected_by=body.inspected_by, notes=body.notes,
    )
    if inspection is None:
        raise HTTPException(status_code=409, detail="Purchase order not found or not in 'received' status")
    # AI-first: every inspection gets a readiness review automatically
    # — the inspector's passed/failed determination is never touched,
    # this only adds an advisory flag on top when notes warrant it.
    background_tasks.add_task(
        publish_event,
        "fleet.inventory",
        "flt.intake_readiness_requested",
        tenant_id,
        {"intake_inspection_id": str(inspection.id), "passed": inspection.passed, "notes": inspection.notes},
    )
    return {"id": str(inspection.id), "passed": inspection.passed}


@router.post("/intake-inspections/{inspection_id}/assess-readiness-now")
async def assess_readiness_now(inspection_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation inspection
    recording already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    inspection = await inventory_engine.get_intake_inspection(db, tenant_id, inspection_id)
    if inspection is None:
        raise HTTPException(status_code=404, detail="Intake inspection not found")
    response = ai_orchestration_tools.request_intake_readiness(tenant_id, str(inspection_id), inspection.passed, inspection.notes)
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response


class ReadinessAssessmentCreate(BaseModel):
    recommendation: str
    reasoning: str = ""
    agent_key: str = "fleet_intelligence_agent"


@router.post("/intake-inspections/{inspection_id}/readiness-assessments")
async def create_readiness_assessment(inspection_id: uuid.UUID, body: ReadinessAssessmentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """This is the agent's tool call for FLT-001 — and it's now the
    autonomous action itself, not just a log entry. Recording the
    assessment and executing it (auto-provision or auto-hold) happen
    in the same call; there is no separate human confirmation step."""
    recommendation = IntakeReadinessRecommendation(body.recommendation)
    assessment = await inventory_engine.record_intake_readiness(db, tenant_id, inspection_id, recommendation, body.reasoning)

    inspection = await inventory_engine.get_intake_inspection(db, tenant_id, inspection_id)
    vehicle_id = None
    if inspection is not None:
        po_result = await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == inspection.purchase_order_id, PurchaseOrder.tenant_id == tenant_id))
        po = po_result.scalar_one_or_none()
        if po is not None and po.vehicle_id is not None:
            vehicle_id = po.vehicle_id
            await inventory_engine.auto_execute_intake_readiness(db, tenant_id, vehicle_id, recommendation, body.reasoning, body.agent_key)

    return {"id": str(assessment.id), "recommendation": assessment.recommendation.value, "vehicle_id": str(vehicle_id) if vehicle_id else None, "executed": vehicle_id is not None}


@router.get("/intake-inspections/{inspection_id}/readiness")
async def get_intake_readiness(inspection_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    assessment = await inventory_engine.get_intake_readiness(db, tenant_id, inspection_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="No readiness assessment on file")
    return {"id": str(assessment.id), "recommendation": assessment.recommendation.value, "reasoning": assessment.reasoning}


@router.post("/vehicles/{vehicle_id}/provision")
async def provision_vehicle(vehicle_id: uuid.UUID, body: ProvisionRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        record = await inventory_engine.provision_vehicle(
            db, tenant_id=tenant_id, vehicle_id=vehicle_id, provisioned_by=body.provisioned_by, card_id=body.card_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": str(record.id), "tls_cert_common_name": record.tls_cert_common_name}


@router.post("/vehicles/{vehicle_id}/certify-class", response_model=VehicleOut)
async def certify_class(vehicle_id: uuid.UUID, body: CertifyRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await inventory_engine.certify_class(db, tenant_id=tenant_id, vehicle_id=vehicle_id, certified_by=body.certified_by)
    except NotProvisionedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/vehicles", response_model=list[VehicleOut])
async def search_vehicles(
    vehicle_class: VehicleClass | None = None, status: VehicleStatus | None = None, hub_id: uuid.UUID | None = None,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    return await inventory_engine.search_vehicles(db, tenant_id, vehicle_class=vehicle_class, status=status, hub_id=hub_id)


@router.get("/vehicles/{vehicle_id}", response_model=VehicleOut)
async def get_vehicle(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    vehicle = await inventory_engine.get_vehicle(db, tenant_id, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


@router.post("/vehicles/{vehicle_id}/status", response_model=VehicleOut)
async def update_status(vehicle_id: uuid.UUID, body: StatusUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        vehicle = await inventory_engine.update_vehicle_status(db, tenant_id, vehicle_id, body.status)
    except NotProvisionedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle
