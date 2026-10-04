"""
Pricing engine — the two-factor dynamic rate model this whole build
exists to get right: SUPPLY (utilization — how full a hub is right
now) and DEMAND (booking velocity and trend — how fast it's filling).
A hub at 70% utilization that's been flat for a week and one that hit
70% in the last six hours are priced identically by a utilization-only
model; this one tells them apart.

recommend_rate is pure computation — it reads real data and returns a
recommendation, it never writes anything, so it's safe to call as
often as needed (e.g. to show a renter a live quote) without side
effects. apply_recommendation is the autonomous action: compute a
recommendation and, if it's meaningfully different from the current
rate, apply it and log the full supply+demand breakdown in the same
call — not just the resulting number.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import DemandTrend, PricingAdjustmentLog
from src.services import demand_engine, reservation_engine, utilization_engine


def compute_supply_multiplier(utilization_pct: float) -> float:
    """Piecewise, explainable: high utilization scales the rate up,
    low utilization scales it down, the broad middle is left alone —
    matches the platform's own previously-documented behavior
    (84% util -> ~1.21x on the old utilization-only model)."""
    settings = get_settings()
    if utilization_pct >= settings.supply_high_util_threshold:
        span = 100 - settings.supply_high_util_threshold
        return 1.0 + (utilization_pct - settings.supply_high_util_threshold) / span * (settings.supply_high_util_max_multiplier - 1.0)
    if utilization_pct <= settings.supply_low_util_threshold:
        span = settings.supply_low_util_threshold
        return 1.0 - (settings.supply_low_util_threshold - utilization_pct) / span * (1.0 - settings.supply_low_util_min_multiplier)
    return 1.0


def compute_demand_multiplier(trend: DemandTrend) -> float:
    """The genuinely new signal: accelerating demand justifies a
    price increase on top of whatever utilization alone would
    suggest; decelerating demand justifies easing off, even at
    unchanged utilization."""
    settings = get_settings()
    if trend == DemandTrend.ACCELERATING:
        return settings.demand_accelerating_multiplier
    if trend == DemandTrend.DECELERATING:
        return settings.demand_decelerating_multiplier
    return 1.0


@dataclass
class RateRecommendation:
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


async def recommend_rate(db: AsyncSession, tenant_id: str, reservation_id: uuid.UUID) -> RateRecommendation:
    """Read-only — computes a recommendation without applying it, so
    it's safe to call for a live quote as often as needed."""
    settings = get_settings()
    reservation = await reservation_engine.get_reservation(db, tenant_id, reservation_id)
    if reservation is None:
        raise ValueError(f"reservation {reservation_id} not found")

    utilization = await utilization_engine.compute_utilization(db, tenant_id, reservation.hub_id, reservation.rental_class.value)
    forecast = await demand_engine.generate_forecast(db, tenant_id, reservation.hub_id, reservation.rental_class.value)

    supply_mult = compute_supply_multiplier(utilization["utilization_pct"])
    demand_mult = compute_demand_multiplier(forecast.trend)
    combined = supply_mult * demand_mult
    combined = max(settings.combined_multiplier_min, min(settings.combined_multiplier_max, combined))

    recommended_rate = round(reservation.daily_rate_usd * combined, 2)
    reasoning = (
        f"{reservation.rental_class.value} util {utilization['utilization_pct']}% -> supply {supply_mult:.2f}x · "
        f"demand trend {forecast.trend.value} ({forecast.booking_velocity_per_day:+.2f} bookings/day) -> demand {demand_mult:.2f}x · "
        f"combined {combined:.2f}x -> ${recommended_rate}/day (was ${reservation.daily_rate_usd}/day)."
    )

    return RateRecommendation(
        reservation_id=reservation_id, current_rate_usd=reservation.daily_rate_usd, recommended_rate_usd=recommended_rate,
        utilization_pct=utilization["utilization_pct"], booking_velocity_per_day=forecast.booking_velocity_per_day, trend=forecast.trend.value,
        supply_multiplier=round(supply_mult, 4), demand_multiplier=round(demand_mult, 4), combined_multiplier=round(combined, 4), reasoning=reasoning,
    )


async def apply_recommendation(db: AsyncSession, tenant_id: str, reservation_id: uuid.UUID, adjusted_by: str, min_change_pct: float = 1.0) -> tuple[RateRecommendation, PricingAdjustmentLog | None]:
    """Fully autonomous: computes the recommendation and, if it
    differs meaningfully from the current rate, applies it and logs
    the full supply+demand breakdown in this same call. A negligible
    change (below min_change_pct) is reported but not applied — not
    every booking needs a price change."""
    recommendation = await recommend_rate(db, tenant_id, reservation_id)
    pct_change = abs(recommendation.recommended_rate_usd - recommendation.current_rate_usd) / recommendation.current_rate_usd * 100 if recommendation.current_rate_usd else 0

    if pct_change < min_change_pct:
        return recommendation, None

    await reservation_engine.modify_reservation(db, tenant_id, reservation_id, recommendation.recommended_rate_usd)

    log = PricingAdjustmentLog(
        tenant_id=tenant_id, reservation_id=reservation_id, old_rate_usd=recommendation.current_rate_usd, new_rate_usd=recommendation.recommended_rate_usd,
        utilization_pct=recommendation.utilization_pct, supply_multiplier=recommendation.supply_multiplier, demand_multiplier=recommendation.demand_multiplier,
        combined_multiplier=recommendation.combined_multiplier, reasoning=recommendation.reasoning, adjusted_by=adjusted_by,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return recommendation, log


async def list_pricing_logs(db: AsyncSession, tenant_id: str, reservation_id: uuid.UUID | None = None):
    from sqlalchemy import select
    query = select(PricingAdjustmentLog).where(PricingAdjustmentLog.tenant_id == tenant_id)
    if reservation_id:
        query = query.where(PricingAdjustmentLog.reservation_id == reservation_id)
    result = await db.execute(query.order_by(PricingAdjustmentLog.adjusted_at.desc()))
    return list(result.scalars().all())
