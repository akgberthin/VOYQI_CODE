"""Rental fleet engine. Fully autonomous CRUD — adding a vehicle to a
hub's fleet is not an access-control decision."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import RentalVehicle, VehicleStatus


async def create_vehicle(db: AsyncSession, tenant_id: str, hub_id: str, rental_class: str, vin: str, base_daily_rate_usd: float) -> RentalVehicle:
    vehicle = RentalVehicle(tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, vin=vin, base_daily_rate_usd=base_daily_rate_usd)
    db.add(vehicle)
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def get_vehicle(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> RentalVehicle | None:
    result = await db.execute(select(RentalVehicle).where(RentalVehicle.id == vehicle_id, RentalVehicle.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_vehicles(db: AsyncSession, tenant_id: str, hub_id: str | None = None, rental_class: str | None = None, status: str | None = None) -> list[RentalVehicle]:
    query = select(RentalVehicle).where(RentalVehicle.tenant_id == tenant_id)
    if hub_id:
        query = query.where(RentalVehicle.hub_id == hub_id)
    if rental_class:
        query = query.where(RentalVehicle.rental_class == rental_class)
    if status:
        query = query.where(RentalVehicle.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())


async def set_vehicle_status(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, status: str) -> RentalVehicle | None:
    vehicle = await get_vehicle(db, tenant_id, vehicle_id)
    if vehicle is None:
        return None
    vehicle.status = status
    await db.commit()
    await db.refresh(vehicle)
    return vehicle
