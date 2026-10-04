"""
FLT-007 Remarketing & Disposition engine.

create_disposition_order_autonomous is the Fleet Intelligence Agent's
fully autonomous path: it writes the recommendation as immediately
ACCEPTED (full audit trail preserved) and creates the real
DispositionOrder in the same atomic call — no separate human
confirmation step. accept/reject_disposition_recommendation remain
available for any recommendation created through the older
create_disposition_channel_recommendation path (e.g. a paused agent
falling back to advisory-only mode).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import (
    DispositionChannel,
    DispositionChannelRecommendation,
    DispositionChannelRecommendationStatus,
    DispositionOrder,
    Vehicle,
)


async def evaluate_disposition_eligibility(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> dict | None:
    settings = get_settings()
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        return None

    acquired_at = vehicle.acquired_at
    if acquired_at.tzinfo is None:
        acquired_at = acquired_at.replace(tzinfo=timezone.utc)
    age_months = int((datetime.now(timezone.utc) - acquired_at).days / 30)

    reasons = []
    if age_months >= settings.disposition_age_months:
        reasons.append(f"age {age_months} months >= threshold {settings.disposition_age_months}")
    if vehicle.odometer_miles >= settings.disposition_mileage:
        reasons.append(f"mileage {vehicle.odometer_miles} >= threshold {settings.disposition_mileage}")

    return {"vehicle_id": str(vehicle_id), "eligible": bool(reasons), "reasons": reasons}


async def create_disposition_order(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, channel: DispositionChannel, created_by: str
) -> DispositionOrder:
    order = DispositionOrder(tenant_id=tenant_id, vehicle_id=vehicle_id, channel=channel, created_by=created_by)
    db.add(order)

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        from src.models import VehicleStatus

        vehicle.status = VehicleStatus.REMARKETING

    await db.commit()
    await db.refresh(order)
    return order


async def record_recovery(db: AsyncSession, tenant_id: str, order_id: uuid.UUID, recovery_amount_usd: float) -> DispositionOrder | None:
    result = await db.execute(select(DispositionOrder).where(DispositionOrder.id == order_id, DispositionOrder.tenant_id == tenant_id))
    order = result.scalar_one_or_none()
    if order is None:
        return None
    order.recovery_amount_usd = recovery_amount_usd
    order.status = "sold"

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == order.vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        from src.models import VehicleStatus

        vehicle.status = VehicleStatus.DISPOSED

    await db.commit()
    await db.refresh(order)
    return order


class RecommendationNotPendingError(ValueError):
    pass


async def get_vehicle(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> Vehicle | None:
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def create_disposition_channel_recommendation(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, recommended_channel: DispositionChannel, reasoning: str,
) -> DispositionChannelRecommendation:
    rec = DispositionChannelRecommendation(tenant_id=tenant_id, vehicle_id=vehicle_id, recommended_channel=recommended_channel, reasoning=reasoning)
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return rec


async def create_disposition_order_autonomous(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, channel: DispositionChannel, reasoning: str, agent_key: str,
) -> tuple[DispositionChannelRecommendation, DispositionOrder]:
    """Fully autonomous FLT-007 execution: the agent's channel choice
    becomes a real DispositionOrder immediately, in the same call that
    records the recommendation (written as ACCEPTED for a complete
    audit trail — no PENDING state, no separate human confirmation)."""
    rec = DispositionChannelRecommendation(
        tenant_id=tenant_id, vehicle_id=vehicle_id, recommended_channel=channel, reasoning=reasoning,
        status=DispositionChannelRecommendationStatus.ACCEPTED,
    )
    db.add(rec)
    order = DispositionOrder(tenant_id=tenant_id, vehicle_id=vehicle_id, channel=channel, created_by=agent_key)
    db.add(order)

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        from src.models import VehicleStatus

        vehicle.status = VehicleStatus.REMARKETING

    await db.commit()
    await db.refresh(rec)
    await db.refresh(order)
    return rec, order


async def list_pending_disposition_recommendations(db: AsyncSession, tenant_id: str) -> list[DispositionChannelRecommendation]:
    result = await db.execute(
        select(DispositionChannelRecommendation).where(DispositionChannelRecommendation.tenant_id == tenant_id, DispositionChannelRecommendation.status == DispositionChannelRecommendationStatus.PENDING)
    )
    return list(result.scalars().all())


async def accept_disposition_recommendation(
    db: AsyncSession, tenant_id: str, recommendation_id: uuid.UUID, created_by: str, channel_override: DispositionChannel | None = None,
) -> tuple[DispositionChannelRecommendation, DispositionOrder]:
    """The only human-confirmed action that turns a channel
    recommendation into a real disposition order. channel_override
    lets fleet ops accept the recommendation's spirit while choosing a
    different channel — the recommendation is a signal, not a
    binding instruction."""
    result = await db.execute(
        select(DispositionChannelRecommendation).where(DispositionChannelRecommendation.id == recommendation_id, DispositionChannelRecommendation.tenant_id == tenant_id)
    )
    rec = result.scalar_one_or_none()
    if rec is None or rec.status != DispositionChannelRecommendationStatus.PENDING:
        raise RecommendationNotPendingError("recommendation not found or already reviewed")

    channel = channel_override or rec.recommended_channel
    order = await create_disposition_order(db, tenant_id, rec.vehicle_id, channel, created_by)
    rec.status = DispositionChannelRecommendationStatus.ACCEPTED
    await db.commit()
    await db.refresh(rec)
    return rec, order


async def reject_disposition_recommendation(db: AsyncSession, tenant_id: str, recommendation_id: uuid.UUID) -> DispositionChannelRecommendation | None:
    result = await db.execute(
        select(DispositionChannelRecommendation).where(DispositionChannelRecommendation.id == recommendation_id, DispositionChannelRecommendation.tenant_id == tenant_id)
    )
    rec = result.scalar_one_or_none()
    if rec is None or rec.status != DispositionChannelRecommendationStatus.PENDING:
        return None
    rec.status = DispositionChannelRecommendationStatus.REJECTED
    await db.commit()
    await db.refresh(rec)
    return rec
