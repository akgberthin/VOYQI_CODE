import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import analytics_engine

router = APIRouter(prefix="/api/v1/fleet", tags=["analytics"])


@router.get("/vehicles/{vehicle_id}/tco")
async def get_vehicle_tco(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await analytics_engine.get_vehicle_tco(db, tenant_id, vehicle_id)


@router.get("/vehicles/{vehicle_id}/availability")
async def get_vehicle_availability(vehicle_id: uuid.UUID, as_of: datetime | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await analytics_engine.get_vehicle_availability(db, tenant_id, vehicle_id, as_of)


@router.get("/analytics/fleet-summary")
async def get_fleet_summary(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await analytics_engine.get_fleet_summary(db, tenant_id)


@router.post("/vehicles/{vehicle_id}/request-fleet-optimization-review", status_code=202)
async def request_fleet_optimization_review(vehicle_id: uuid.UUID, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    signals = await analytics_engine.gather_fleet_optimization_signals(db, tenant_id, vehicle_id)
    background_tasks.add_task(
        publish_event,
        "fleet.analytics",
        "flt.fleet_optimization_requested",
        tenant_id,
        signals,
    )
    return {"vehicle_id": str(vehicle_id), "status": "review_requested"}


@router.post("/vehicles/{vehicle_id}/review-fleet-optimization-now")
async def review_fleet_optimization_now(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation the request
    endpoint already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    signals = await analytics_engine.gather_fleet_optimization_signals(db, tenant_id, vehicle_id)
    response = ai_orchestration_tools.request_fleet_optimization(tenant_id, signals)
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response


class FleetOptimizationCreate(BaseModel):
    recommendation: str
    reasoning: str = ""


@router.post("/vehicles/{vehicle_id}/fleet-optimization-assessments")
async def create_fleet_optimization_assessment(vehicle_id: uuid.UUID, body: FleetOptimizationCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    from src.models import FleetOptimizationRecommendation

    assessment = await analytics_engine.record_fleet_optimization(db, tenant_id, vehicle_id, FleetOptimizationRecommendation(body.recommendation), body.reasoning)
    return {"id": str(assessment.id), "recommendation": assessment.recommendation.value}


@router.get("/vehicles/{vehicle_id}/fleet-optimization")
async def get_fleet_optimization(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    assessment = await analytics_engine.get_latest_fleet_optimization(db, tenant_id, vehicle_id)
    if assessment is None:
        raise HTTPException(status_code=404, detail="No fleet optimization assessment on file")
    return {"id": str(assessment.id), "recommendation": assessment.recommendation.value, "reasoning": assessment.reasoning}
