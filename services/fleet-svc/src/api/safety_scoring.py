import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import DrivingEventType
from src.services import safety_scoring_engine

router = APIRouter(prefix="/api/v1/fleet", tags=["safety-scoring"])

# AI-first, but not on every single event — a lone harsh brake isn't
# worth an agent call. Re-assessing every 5th event logged is a
# reasonable, real trigger point without being noisy.
REASSESSMENT_EVENT_INTERVAL = 5


class DrivingEventLog(BaseModel):
    vehicle_id: uuid.UUID
    event_type: DrivingEventType


class DrivingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    event_type: DrivingEventType
    occurred_at: datetime


class SafetyScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    score: float
    reasoning: str
    window_days: int
    computed_at: datetime


@router.post("/driving-events", response_model=DrivingEventOut, status_code=201)
async def log_driving_event(
    body: DrivingEventLog, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    event = await safety_scoring_engine.log_driving_event(db, tenant_id, body.vehicle_id, body.event_type)
    # AI-first: re-assess automatically once enough new events have
    # accumulated — not held until a fleet manager remembers to check.
    recent_events = await safety_scoring_engine.list_driving_events(db, tenant_id, body.vehicle_id)
    if len(recent_events) % REASSESSMENT_EVENT_INTERVAL == 0:
        event_counts: dict[str, int] = {}
        for e in recent_events:
            event_counts[e.event_type.value] = event_counts.get(e.event_type.value, 0) + 1
        background_tasks.add_task(
            publish_event,
            "fleet.safety",
            "flt.safety_score_requested",
            tenant_id,
            {"vehicle_id": str(body.vehicle_id), "event_counts": event_counts, "window_days": 30},
        )
    return event


@router.get("/vehicles/{vehicle_id}/driving-events", response_model=list[DrivingEventOut])
async def list_driving_events(vehicle_id: uuid.UUID, window_days: int = 30, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await safety_scoring_engine.list_driving_events(db, tenant_id, vehicle_id, window_days)


@router.post("/vehicles/{vehicle_id}/safety-scores", response_model=SafetyScoreOut, status_code=201)
async def record_safety_score(vehicle_id: uuid.UUID, score: float, reasoning: str = "", window_days: int = 30, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await safety_scoring_engine.record_safety_score(db, tenant_id, vehicle_id, score, reasoning, window_days)


@router.get("/vehicles/{vehicle_id}/safety-scores/latest", response_model=SafetyScoreOut | None)
async def get_latest_safety_score(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await safety_scoring_engine.get_latest_safety_score(db, tenant_id, vehicle_id)


@router.get("/safety-scores/watchlist", response_model=list[SafetyScoreOut])
async def get_safety_watchlist(threshold: float = 60.0, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await safety_scoring_engine.list_vehicles_below_score(db, tenant_id, threshold)


@router.post("/vehicles/{vehicle_id}/assess-safety-now")
async def assess_safety_now(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation event
    logging already triggers asynchronously once enough events
    accumulate."""
    from src.services import ai_orchestration_tools

    events = await safety_scoring_engine.list_driving_events(db, tenant_id, vehicle_id)
    event_counts: dict[str, int] = {}
    for e in events:
        event_counts[e.event_type.value] = event_counts.get(e.event_type.value, 0) + 1
    response = ai_orchestration_tools.request_safety_score(tenant_id, str(vehicle_id), event_counts, 30)
    if response is None:
        return {"error": "Fleet Intelligence Agent unreachable"}
    return response
