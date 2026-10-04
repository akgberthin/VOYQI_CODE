"""
Insight engine — turns the same demand+supply data the pricing engine
uses into plain-language, actionable guidance for two different
audiences: the renter deciding whether to book now, and the hub
administrator deciding whether capacity needs attention. Fully
autonomous — generating an insight is not an access-control decision,
and every insight is derived from real utilization/forecast data, not
a separate guess layered on top.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import (
    AdminInsight,
    AdminInsightSeverity,
    AdminInsightType,
    ClientInsight,
    ClientInsightType,
    DemandTrend,
)
from src.services import demand_engine, utilization_engine


async def generate_client_insights(db: AsyncSession, tenant_id: str, hub_id: str, rental_class: str) -> list[ClientInsight]:
    """Instructive to the renter: the same forecast the pricing
    engine reads, translated into a plain-language nudge rather than
    a raw percentage the renter would have to interpret themselves."""
    settings = get_settings()
    utilization = await utilization_engine.compute_utilization(db, tenant_id, hub_id, rental_class)
    forecast = await demand_engine.get_latest_forecast(db, tenant_id, hub_id, rental_class) or await demand_engine.generate_forecast(db, tenant_id, hub_id, rental_class)

    insights = []
    if forecast.forecasted_utilization_pct >= settings.low_availability_client_threshold_pct:
        insights.append(ClientInsight(
            tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, insight_type=ClientInsightType.LOW_AVAILABILITY,
            message=f"Availability is tight for {rental_class} at this hub — projected to reach {forecast.forecasted_utilization_pct}% booked within the next few days.",
        ))
    if forecast.trend == DemandTrend.ACCELERATING:
        insights.append(ClientInsight(
            tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, insight_type=ClientInsightType.PRICE_RISING,
            message=f"Demand for {rental_class} is picking up here — booking now typically locks in a lower rate than waiting.",
        ))
    elif forecast.trend == DemandTrend.DECELERATING and utilization["utilization_pct"] < settings.supply_low_util_threshold:
        insights.append(ClientInsight(
            tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, insight_type=ClientInsightType.GOOD_VALUE,
            message=f"{rental_class.capitalize()} availability is strong at this hub right now — a good time to book at a favorable rate.",
        ))

    for i in insights:
        db.add(i)
    if insights:
        await db.commit()
        for i in insights:
            await db.refresh(i)
    return insights


async def generate_admin_insights(db: AsyncSession, tenant_id: str, hub_id: str, rental_class: str) -> list[AdminInsight]:
    """Instructive to the hub administrator: a predictive, actionable
    flag rather than a metric they'd have to interpret and act on
    themselves."""
    settings = get_settings()
    utilization = await utilization_engine.compute_utilization(db, tenant_id, hub_id, rental_class)
    forecast = await demand_engine.get_latest_forecast(db, tenant_id, hub_id, rental_class) or await demand_engine.generate_forecast(db, tenant_id, hub_id, rental_class)

    insights = []
    if forecast.forecasted_utilization_pct >= settings.capacity_risk_threshold_pct:
        severity = AdminInsightSeverity.CRITICAL if forecast.forecasted_utilization_pct >= 98 else AdminInsightSeverity.WARNING
        insights.append(AdminInsight(
            tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, insight_type=AdminInsightType.CAPACITY_RISK, severity=severity,
            message=f"{rental_class.capitalize()} at this hub is projected to hit {forecast.forecasted_utilization_pct}% utilization within {settings.forecast_horizon_days} days at the current booking pace ({forecast.booking_velocity_per_day:+.2f}/day). Consider adding capacity or reallocating from an underutilized hub.",
        ))
    elif utilization["utilization_pct"] <= settings.underutilized_threshold_pct:
        insights.append(AdminInsight(
            tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, insight_type=AdminInsightType.UNDERUTILIZED, severity=AdminInsightSeverity.INFO,
            message=f"{rental_class.capitalize()} at this hub is at {utilization['utilization_pct']}% utilization — a reallocation candidate if another hub of the same class is approaching capacity.",
        ))

    if forecast.trend == DemandTrend.ACCELERATING and utilization["utilization_pct"] >= settings.supply_high_util_threshold:
        insights.append(AdminInsight(
            tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, insight_type=AdminInsightType.DEMAND_SPIKE, severity=AdminInsightSeverity.WARNING,
            message=f"Demand spike detected for {rental_class} at this hub — utilization is already high ({utilization['utilization_pct']}%) and accelerating ({forecast.booking_velocity_per_day:+.2f} bookings/day).",
        ))

    for i in insights:
        db.add(i)
    if insights:
        await db.commit()
        for i in insights:
            await db.refresh(i)
    return insights


async def list_client_insights(db: AsyncSession, tenant_id: str, hub_id: str | None = None, rental_class: str | None = None) -> list[ClientInsight]:
    query = select(ClientInsight).where(ClientInsight.tenant_id == tenant_id)
    if hub_id:
        query = query.where(ClientInsight.hub_id == hub_id)
    if rental_class:
        query = query.where(ClientInsight.rental_class == rental_class)
    result = await db.execute(query.order_by(ClientInsight.generated_at.desc()))
    return list(result.scalars().all())


async def list_admin_insights(db: AsyncSession, tenant_id: str, hub_id: str | None = None, resolved: bool | None = None) -> list[AdminInsight]:
    query = select(AdminInsight).where(AdminInsight.tenant_id == tenant_id)
    if hub_id:
        query = query.where(AdminInsight.hub_id == hub_id)
    if resolved is not None:
        query = query.where(AdminInsight.resolved == resolved)
    result = await db.execute(query.order_by(AdminInsight.generated_at.desc()))
    return list(result.scalars().all())


async def resolve_admin_insight(db: AsyncSession, tenant_id: str, insight_id: uuid.UUID) -> AdminInsight | None:
    result = await db.execute(select(AdminInsight).where(AdminInsight.id == insight_id, AdminInsight.tenant_id == tenant_id))
    insight = result.scalar_one_or_none()
    if insight is None:
        return None
    insight.resolved = True
    await db.commit()
    await db.refresh(insight)
    return insight
