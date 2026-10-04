import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import DispositionChannel
from src.services import remarketing_engine

router = APIRouter(prefix="/api/v1/fleet", tags=["remarketing"])


class DispositionCreate(BaseModel):
    vehicle_id: uuid.UUID
    channel: DispositionChannel
    created_by: str


class DispositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    channel: DispositionChannel
    recovery_amount_usd: float | None
    status: str


class RecoveryRecord(BaseModel):
    recovery_amount_usd: float


@router.get("/vehicles/{vehicle_id}/disposition-eligibility")
async def evaluate_eligibility(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await remarketing_engine.evaluate_disposition_eligibility(db, tenant_id, vehicle_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return result


@router.post("/disposition-orders", response_model=DispositionOut, status_code=201)
async def create_disposition_order(body: DispositionCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await remarketing_engine.create_disposition_order(db, tenant_id, body.vehicle_id, body.channel, body.created_by)


@router.post("/disposition-orders/{order_id}/recovery", response_model=DispositionOut)
async def record_recovery(order_id: uuid.UUID, body: RecoveryRecord, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    order = await remarketing_engine.record_recovery(db, tenant_id, order_id, body.recovery_amount_usd)
    if order is None:
        raise HTTPException(status_code=404, detail="Disposition order not found")
    return order


@router.post("/vehicles/{vehicle_id}/request-disposition-recommendation", status_code=202)
async def request_disposition_recommendation(vehicle_id: uuid.UUID, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    vehicle = await remarketing_engine.get_vehicle(db, tenant_id, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    eligibility = await remarketing_engine.evaluate_disposition_eligibility(db, tenant_id, vehicle_id)
    if not eligibility["eligible"]:
        raise HTTPException(status_code=409, detail="Vehicle is not disposition-eligible")
    background_tasks.add_task(
        publish_event,
        "fleet.remarketing",
        "flt.disposition_channel_requested",
        tenant_id,
        {"vehicle_id": str(vehicle_id), "odometer_miles": vehicle.odometer_miles, "vehicle_class": vehicle.vehicle_class.value},
    )
    return {"vehicle_id": str(vehicle_id), "status": "recommendation_requested"}


@router.post("/vehicles/{vehicle_id}/recommend-disposition-channel-now")
async def recommend_disposition_channel_now(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation the request
    endpoint already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    vehicle = await remarketing_engine.get_vehicle(db, tenant_id, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    response = ai_orchestration_tools.request_disposition_channel(tenant_id, str(vehicle_id), vehicle.odometer_miles, vehicle.vehicle_class.value)
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response


class DispositionRecommendationCreate(BaseModel):
    channel: str
    reasoning: str = ""
    agent_key: str = "fleet_intelligence_agent"


@router.post("/vehicles/{vehicle_id}/disposition-recommendations")
async def create_disposition_recommendation(vehicle_id: uuid.UUID, body: DispositionRecommendationCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The agent's FLT-007 tool call — this is now the fully autonomous
    path: the channel recommendation is written as ACCEPTED and a real
    DispositionOrder is created in the same call, no human accept step.
    The vehicle moves to REMARKETING immediately."""
    rec, order = await remarketing_engine.create_disposition_order_autonomous(
        db, tenant_id, vehicle_id, DispositionChannel(body.channel), body.reasoning, body.agent_key,
    )
    return {
        "recommendation_id": str(rec.id), "recommended_channel": rec.recommended_channel.value,
        "order_id": str(order.id), "channel": order.channel.value, "status": rec.status.value,
    }


@router.get("/disposition-recommendations/pending")
async def list_pending_disposition_recommendations(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Retained for the paused-agent advisory-only fallback path —
    under normal (ACTIVE) operation, create_disposition_recommendation
    above never leaves anything PENDING, so this will normally be
    empty."""
    recs = await remarketing_engine.list_pending_disposition_recommendations(db, tenant_id)
    return [{"id": str(r.id), "vehicle_id": str(r.vehicle_id), "recommended_channel": r.recommended_channel.value, "reasoning": r.reasoning} for r in recs]


class AcceptDispositionRecommendation(BaseModel):
    created_by: str
    channel_override: str | None = None


@router.post("/disposition-recommendations/{recommendation_id}/accept")
async def accept_disposition_recommendation(recommendation_id: uuid.UUID, body: AcceptDispositionRecommendation, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        rec, order = await remarketing_engine.accept_disposition_recommendation(
            db, tenant_id, recommendation_id, body.created_by, DispositionChannel(body.channel_override) if body.channel_override else None,
        )
    except remarketing_engine.RecommendationNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"recommendation_id": str(rec.id), "status": rec.status.value, "order_id": str(order.id), "channel": order.channel.value}


@router.post("/disposition-recommendations/{recommendation_id}/reject")
async def reject_disposition_recommendation(recommendation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    rec = await remarketing_engine.reject_disposition_recommendation(db, tenant_id, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=409, detail="Recommendation not found or already reviewed")
    return {"id": str(rec.id), "status": rec.status.value}
