"""
DMS-003 — Sales Desk & Deal Manager engine.

desk_and_approve is fully autonomous: it prices the deal and checks it
against the gross floor in one call. A price that misses the floor is
auto-countered upward and approved in the same pass — never silently
approved below floor, and never left for a manager to review.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import Deal, DealStatus, Vehicle


async def create_deal(db: AsyncSession, tenant_id: str, rooftop_id: uuid.UUID, vehicle_id: uuid.UUID, buyer_name: str) -> Deal:
    deal = Deal(tenant_id=tenant_id, rooftop_id=rooftop_id, vehicle_id=vehicle_id, buyer_name=buyer_name)
    db.add(deal)

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        from src.models import VehicleStatus
        vehicle.status = VehicleStatus.PENDING_SALE

    await db.commit()
    await db.refresh(deal)
    return deal


async def get_deal(db: AsyncSession, tenant_id: str, deal_id: uuid.UUID) -> Deal | None:
    result = await db.execute(select(Deal).where(Deal.id == deal_id, Deal.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_deals(db: AsyncSession, tenant_id: str) -> list[Deal]:
    result = await db.execute(select(Deal).where(Deal.tenant_id == tenant_id))
    return list(result.scalars().all())


async def desk_and_approve(db: AsyncSession, tenant_id: str, deal_id: uuid.UUID) -> Deal:
    """Fully autonomous: computes the desk price, checks the gross
    floor, and — if the price misses it — auto-counters to the floor,
    all in one call. status is always APPROVED when this returns;
    there is no intermediate DESKING state a human needs to act on."""
    settings = get_settings()
    deal = await get_deal(db, tenant_id, deal_id)
    if deal is None:
        raise ValueError(f"deal {deal_id} not found")

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == deal.vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle is None:
        raise ValueError(f"vehicle {deal.vehicle_id} not found")

    desk_price = round(vehicle.price_usd * 0.97, 2)
    gross_pct = (desk_price - vehicle.cost_usd) / vehicle.cost_usd if vehicle.cost_usd else 0.0

    if gross_pct >= settings.min_gross_floor_pct:
        deal.price_usd = desk_price
    else:
        # Auto-counter upward to exactly clear the floor, then approve
        # in the same pass — not rejected, not queued.
        deal.price_usd = round(vehicle.cost_usd * (1 + settings.min_gross_floor_pct), 2)

    deal.status = DealStatus.APPROVED
    await db.commit()
    await db.refresh(deal)
    return deal


async def deliver_deal(db: AsyncSession, tenant_id: str, deal_id: uuid.UUID) -> Deal:
    deal = await get_deal(db, tenant_id, deal_id)
    if deal is None:
        raise ValueError(f"deal {deal_id} not found")
    deal.status = DealStatus.DELIVERED

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == deal.vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        from src.models import VehicleStatus
        vehicle.status = VehicleStatus.SOLD

    await db.commit()
    await db.refresh(deal)
    return deal
