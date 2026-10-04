"""
FLT-010 Driving Behavior & Safety Scoring — inspired by Motive's AI
dashcam event detection, scoped to what actually applies to a
subscription passenger fleet (no HOS/ELD/commercial-driver regulatory
tracking — this isn't a trucking operation).

log_driving_event is a deterministic fact — an event either happened
or it didn't. request_safety_score_assessment publishes
flt.safety_score_requested unconditionally; the Fleet Intelligence
Agent's weighted-event heuristic is the deterministic floor, mirroring
the Member Experience Agent's churn-scoring discipline exactly. The
score is a signal for fleet ops and pricing to act on — never an
automatic penalty applied to a member's account.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import DrivingEvent, DrivingEventType, SafetyScoreAssessment


async def log_driving_event(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, event_type: DrivingEventType) -> DrivingEvent:
    event = DrivingEvent(tenant_id=tenant_id, vehicle_id=vehicle_id, event_type=event_type)
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def list_driving_events(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, window_days: int = 30) -> list[DrivingEvent]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    result = await db.execute(
        select(DrivingEvent).where(DrivingEvent.tenant_id == tenant_id, DrivingEvent.vehicle_id == vehicle_id, DrivingEvent.occurred_at >= cutoff)
    )
    return list(result.scalars().all())


async def record_safety_score(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, score: float, reasoning: str, window_days: int = 30) -> SafetyScoreAssessment:
    """The only function that ever writes a SafetyScoreAssessment —
    always from the Fleet Intelligence Agent's evaluation, always
    logged with reasoning."""
    assessment = SafetyScoreAssessment(tenant_id=tenant_id, vehicle_id=vehicle_id, score=score, reasoning=reasoning, window_days=window_days)
    db.add(assessment)
    await db.commit()
    await db.refresh(assessment)
    return assessment


async def get_latest_safety_score(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> SafetyScoreAssessment | None:
    result = await db.execute(
        select(SafetyScoreAssessment).where(SafetyScoreAssessment.tenant_id == tenant_id, SafetyScoreAssessment.vehicle_id == vehicle_id)
        .order_by(SafetyScoreAssessment.computed_at.desc())
    )
    return result.scalars().first()


async def list_vehicles_below_score(db: AsyncSession, tenant_id: str, threshold: float) -> list[SafetyScoreAssessment]:
    """Fleet ops watchlist — every vehicle's most recent score below
    the threshold. Plain filtering, no AI."""
    result = await db.execute(select(SafetyScoreAssessment).where(SafetyScoreAssessment.tenant_id == tenant_id))
    all_scores = list(result.scalars().all())
    latest_by_vehicle: dict[uuid.UUID, SafetyScoreAssessment] = {}
    for s in all_scores:
        existing = latest_by_vehicle.get(s.vehicle_id)
        if existing is None or s.computed_at > existing.computed_at:
            latest_by_vehicle[s.vehicle_id] = s
    return [s for s in latest_by_vehicle.values() if s.score < threshold]
