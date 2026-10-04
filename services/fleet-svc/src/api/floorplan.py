import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import floorplan_engine
from src.services.floorplan_engine import InsufficientCapacityError

router = APIRouter(prefix="/api/v1/fleet", tags=["floorplan"])


class FacilityCreate(BaseModel):
    lender: str
    credit_limit_usd: float
    max_utilization_pct: float = 90.0


class FacilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lender: str
    credit_limit_usd: float
    utilized_amount_usd: float
    max_utilization_pct: float
    frozen: bool
    frozen_reason: str


class PledgeRequest(BaseModel):
    vehicle_id: uuid.UUID
    facility_id: uuid.UUID
    pledged_amount_usd: float


class CollateralOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    facility_id: uuid.UUID
    pledged_amount_usd: float
    status: str


@router.post("/floorplan-facilities", response_model=FacilityOut, status_code=201)
async def create_facility(body: FacilityCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await floorplan_engine.create_facility(
        db, tenant_id=tenant_id, lender=body.lender, credit_limit_usd=body.credit_limit_usd,
        max_utilization_pct=body.max_utilization_pct,
    )


@router.post("/vin-collateral/pledge", response_model=CollateralOut, status_code=201)
async def pledge_vehicle(
    body: PledgeRequest,
    background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        collateral, breached = await floorplan_engine.pledge_vehicle(
            db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, facility_id=body.facility_id,
            pledged_amount_usd=body.pledged_amount_usd,
        )
    except InsufficientCapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except floorplan_engine.FacilityFrozenError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if breached:
        background_tasks.add_task(
            publish_event,
            "fleet.floorplan",
            "floorplan.covenant_breached",
            tenant_id,
            {
                "facility_id": str(body.facility_id), "vehicle_id": str(body.vehicle_id),
                "pledged_amount_usd": body.pledged_amount_usd,
            },
        )
    # AI-first: every pledge gets a covenant risk assessment, not just
    # outright breaches — this is what gives finance an early
    # "approaching the limit" warning before the hard breach happens.
    # is_covenant_breached itself and every dollar figure remain
    # exactly the deterministic arithmetic they already were.
    facility = await floorplan_engine.get_facility(db, tenant_id, body.facility_id)
    background_tasks.add_task(
        publish_event,
        "fleet.floorplan",
        "flt.covenant_risk_requested",
        tenant_id,
        {"facility_id": str(body.facility_id), "utilization_pct": floorplan_engine.utilization_pct(facility), "max_utilization_pct": facility.max_utilization_pct},
    )
    return collateral


@router.post("/vin-collateral/{collateral_id}/release", response_model=CollateralOut)
async def release_vehicle(collateral_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    collateral = await floorplan_engine.release_vehicle(db, tenant_id, collateral_id)
    if collateral is None:
        raise HTTPException(status_code=409, detail="Collateral not found or already released")
    return collateral


class UnfreezeRequest(BaseModel):
    unfrozen_by: str


@router.post("/floorplan-facilities/{facility_id}/unfreeze", response_model=FacilityOut)
async def unfreeze_facility(facility_id: uuid.UUID, body: UnfreezeRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Human-only action — clearing a lender covenant breach is an
    attestation a human needs to make, the one deliberate exception to
    full automation in this module."""
    facility = await floorplan_engine.unfreeze_facility(db, tenant_id, facility_id)
    if facility is None:
        raise HTTPException(status_code=404, detail="Facility not found")
    return facility


@router.post("/floorplan-facilities/{facility_id}/assess-covenant-risk-now")
async def assess_covenant_risk_now(facility_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation pledging
    already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    facility = await floorplan_engine.get_facility(db, tenant_id, facility_id)
    if facility is None:
        raise HTTPException(status_code=404, detail="Facility not found")
    response = ai_orchestration_tools.request_covenant_risk(tenant_id, str(facility_id), floorplan_engine.utilization_pct(facility), facility.max_utilization_pct)
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response


class CovenantRiskCreate(BaseModel):
    risk_level: str
    reasoning: str = ""
    agent_key: str = "fleet_intelligence_agent"


@router.post("/floorplan-facilities/{facility_id}/covenant-risk-assessments")
async def create_covenant_risk_assessment(facility_id: uuid.UUID, body: CovenantRiskCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The agent's FLT-008/009 tool call — recording the risk read and
    autonomously freezing the facility on a BREACHED verdict happen in
    the same call. No human confirmation gate on the freeze itself;
    unfreezing remains the one deliberate human-only action here."""
    from src.models import CovenantRiskLevel

    level = CovenantRiskLevel(body.risk_level)
    assessment = await floorplan_engine.record_covenant_risk(db, tenant_id, facility_id, level, body.reasoning)
    frozen = False
    if level == CovenantRiskLevel.BREACHED:
        facility = await floorplan_engine.auto_freeze_facility(db, tenant_id, facility_id, body.reasoning, body.agent_key)
        frozen = facility is not None and facility.frozen
    return {"id": str(assessment.id), "risk_level": assessment.risk_level.value, "auto_frozen": frozen}


@router.get("/floorplan-facilities/{facility_id}/covenant-risk")
async def get_covenant_risk(facility_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    assessment = await floorplan_engine.get_latest_covenant_risk(db, tenant_id, facility_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="No covenant risk assessment on file")
    return {"id": str(assessment.id), "risk_level": assessment.risk_level.value, "reasoning": assessment.reasoning}
