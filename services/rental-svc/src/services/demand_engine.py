"""
Demand engine — the signal utilization alone cannot provide: how fast
a hub is filling, not just how full it is right now. Two hubs at 70%
utilization can be in genuinely different positions — one flat for a
week, one that hit 70% in the last six hours — and only booking
velocity and trend tell them apart.

generate_forecast is the predictive-analytics piece: a real,
deterministic projection of utilization N days out, computed from
current occupancy plus the recent booking rate, not a guess.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import BookingEvent, BookingEventType, DemandForecast, DemandTrend
from src.services import utilization_engine


async def compute_booking_velocity(db: AsyncSession, tenant_id: str, hub_id: str, rental_class: str, window_days: int, end_at: datetime | None = None) -> float:
    """Net bookings per day over a rolling window — a cancellation
    reduces velocity the same way a booking increases it, since both
    are real demand signals."""
    end_at = end_at or datetime.now(timezone.utc)
    start_at = end_at - timedelta(days=window_days)
    result = await db.execute(
        select(BookingEvent).where(
            BookingEvent.tenant_id == tenant_id, BookingEvent.hub_id == hub_id, BookingEvent.rental_class == rental_class,
            BookingEvent.occurred_at >= start_at, BookingEvent.occurred_at <= end_at,
        )
    )
    events = list(result.scalars().all())
    net = sum(1 if e.event_type == BookingEventType.BOOKED else -1 for e in events)
    return round(net / window_days, 2) if window_days else 0.0


async def classify_trend(db: AsyncSession, tenant_id: str, hub_id: str, rental_class: str) -> DemandTrend:
    """Compares the most recent comparison window's velocity against
    the window immediately before it — a real before/after comparison,
    not a single-window snapshot mistaken for a trend.

    A zero prior-window baseline is treated as STEADY, not
    automatically ACCELERATING, unless the recent velocity clears a
    minimum noise floor — the same caution ab_test_engine's minimum
    sample size applies elsewhere on this platform. Without that
    floor, a single isolated booking after a quiet period would read
    as a demand spike and justify a price premium on noise, not signal."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    recent_velocity = await compute_booking_velocity(db, tenant_id, hub_id, rental_class, settings.demand_trend_comparison_days, end_at=now)
    prior_end = now - timedelta(days=settings.demand_trend_comparison_days)
    prior_velocity = await compute_booking_velocity(db, tenant_id, hub_id, rental_class, settings.demand_trend_comparison_days, end_at=prior_end)

    if prior_velocity == 0:
        if recent_velocity >= settings.demand_min_velocity_for_zero_baseline_trend:
            return DemandTrend.ACCELERATING
        return DemandTrend.STEADY

    pct_change = ((recent_velocity - prior_velocity) / abs(prior_velocity)) * 100
    if pct_change >= settings.demand_trend_threshold_pct:
        return DemandTrend.ACCELERATING
    if pct_change <= -settings.demand_trend_threshold_pct:
        return DemandTrend.DECELERATING
    return DemandTrend.STEADY


async def generate_forecast(db: AsyncSession, tenant_id: str, hub_id: str, rental_class: str) -> DemandForecast:
    """Fully autonomous: a real deterministic projection, not a
    guess — current occupancy plus the recent net booking rate,
    projected across the configured horizon and capped at capacity."""
    settings = get_settings()
    current = await utilization_engine.compute_utilization(db, tenant_id, hub_id, rental_class)
    velocity = await compute_booking_velocity(db, tenant_id, hub_id, rental_class, settings.demand_velocity_window_days)
    trend = await classify_trend(db, tenant_id, hub_id, rental_class)

    total = current["total_count"]
    projected_bookings = velocity * settings.forecast_horizon_days
    projected_booked = max(0, min(total, current["booked_count"] + projected_bookings))
    forecasted_utilization_pct = round((projected_booked / total) * 100, 1) if total else 0.0

    reasoning = (
        f"[deterministic] Current utilization {current['utilization_pct']}% ({current['booked_count']}/{total}) · "
        f"net booking velocity {velocity:+.2f}/day over the last {settings.demand_velocity_window_days} days · "
        f"trend {trend.value} · projected to {forecasted_utilization_pct}% in {settings.forecast_horizon_days} days."
    )

    forecast = DemandForecast(
        tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, booking_velocity_per_day=velocity,
        trend=trend, forecasted_utilization_pct=forecasted_utilization_pct, reasoning=reasoning,
    )
    db.add(forecast)
    await db.commit()
    await db.refresh(forecast)
    return forecast


async def get_latest_forecast(db: AsyncSession, tenant_id: str, hub_id: str, rental_class: str) -> DemandForecast | None:
    result = await db.execute(
        select(DemandForecast).where(DemandForecast.tenant_id == tenant_id, DemandForecast.hub_id == hub_id, DemandForecast.rental_class == rental_class)
        .order_by(DemandForecast.generated_at.desc())
    )
    return result.scalars().first()


async def list_forecasts(db: AsyncSession, tenant_id: str, hub_id: str | None = None) -> list[DemandForecast]:
    query = select(DemandForecast).where(DemandForecast.tenant_id == tenant_id)
    if hub_id:
        query = query.where(DemandForecast.hub_id == hub_id)
    result = await db.execute(query.order_by(DemandForecast.generated_at.desc()))
    return list(result.scalars().all())
