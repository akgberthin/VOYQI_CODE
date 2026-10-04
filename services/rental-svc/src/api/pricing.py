import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import pricing_engine

router = APIRouter(prefix="/api/v1/rental/pricing", tags=["pricing"])


class RecommendationOut(BaseModel):
    reservation_id: uuid.UUID
    current_rate_usd: float
    recommended_rate_usd: float
    utilization_pct: float
    booking_velocity_per_day: float
    trend: str
    supply_multiplier: float
    demand_multiplier: float
    combined_multiplier: float
    reasoning: str


@router.get("/reservations/{reservation_id}/recommendation", response_model=RecommendationOut)
async def recommend_rate(reservation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Read-only — the two-factor supply+demand computation, safe to
    call as often as needed (e.g. a live quote) with no side effects."""
    try:
        rec = await pricing_engine.recommend_rate(db, tenant_id, reservation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RecommendationOut(
        reservation_id=rec.reservation_id, current_rate_usd=rec.current_rate_usd, recommended_rate_usd=rec.recommended_rate_usd,
        utilization_pct=rec.utilization_pct, booking_velocity_per_day=rec.booking_velocity_per_day, trend=rec.trend,
        supply_multiplier=rec.supply_multiplier, demand_multiplier=rec.demand_multiplier, combined_multiplier=rec.combined_multiplier, reasoning=rec.reasoning,
    )


class ApplyRequest(BaseModel):
    adjusted_by: str = "pricing_intelligence_agent"
    min_change_pct: float = 1.0


class ApplyResponse(BaseModel):
    recommendation: RecommendationOut
    applied: bool
    log_id: uuid.UUID | None


@router.post("/reservations/{reservation_id}/apply-recommendation", response_model=ApplyResponse)
async def apply_recommendation(reservation_id: uuid.UUID, body: ApplyRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: computes the recommendation and, if it
    differs meaningfully from the current rate, applies it and logs
    the full supply+demand breakdown in this same call."""
    try:
        rec, log = await pricing_engine.apply_recommendation(db, tenant_id, reservation_id, body.adjusted_by, body.min_change_pct)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ApplyResponse(
        recommendation=RecommendationOut(
            reservation_id=rec.reservation_id, current_rate_usd=rec.current_rate_usd, recommended_rate_usd=rec.recommended_rate_usd,
            utilization_pct=rec.utilization_pct, booking_velocity_per_day=rec.booking_velocity_per_day, trend=rec.trend,
            supply_multiplier=rec.supply_multiplier, demand_multiplier=rec.demand_multiplier, combined_multiplier=rec.combined_multiplier, reasoning=rec.reasoning,
        ),
        applied=log is not None, log_id=log.id if log else None,
    )


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    reservation_id: uuid.UUID
    old_rate_usd: float
    new_rate_usd: float
    utilization_pct: float
    supply_multiplier: float
    demand_multiplier: float
    combined_multiplier: float
    reasoning: str
    adjusted_by: str
    adjusted_at: datetime


@router.get("/logs", response_model=list[LogOut])
async def list_pricing_logs(reservation_id: uuid.UUID | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await pricing_engine.list_pricing_logs(db, tenant_id, reservation_id)
