"""
Reservation engine.

Fully autonomous: creating a reservation records the real demand
signal (BookingEvent) in the same call, and confirming one
autonomously allocates the first available vehicle matching the
hub+class — no human picks which physical vehicle a renter gets.

modify_reservation is the one place a real, structural rule is
enforced rather than left to convention: a reservation that has
reached ACTIVE or COMPLETED can never have its rate changed. This
raises rather than silently no-oping, so a caller can't mistake a
refused change for a successful one — the same discipline as every
other hard rule on this platform.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import BookingEvent, BookingEventType, Reservation, ReservationStatus, RentalVehicle, VehicleStatus


class ReservationLockedError(ValueError):
    pass


class NoVehicleAvailableError(ValueError):
    pass


async def create_reservation(
    db: AsyncSession, tenant_id: str, hub_id: str, rental_class: str, customer_name: str,
    start_date: datetime, end_date: datetime, daily_rate_usd: float,
) -> Reservation:
    reservation = Reservation(
        tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, customer_name=customer_name,
        start_date=start_date, end_date=end_date, daily_rate_usd=daily_rate_usd,
    )
    db.add(reservation)
    # The real demand signal is recorded the instant someone books,
    # not when a vehicle is later allocated — that's what booking
    # velocity is actually measuring.
    db.add(BookingEvent(tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, event_type=BookingEventType.BOOKED))
    await db.commit()
    await db.refresh(reservation)
    return reservation


async def get_reservation(db: AsyncSession, tenant_id: str, reservation_id: uuid.UUID) -> Reservation | None:
    result = await db.execute(select(Reservation).where(Reservation.id == reservation_id, Reservation.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_reservations(db: AsyncSession, tenant_id: str, hub_id: str | None = None, status: str | None = None) -> list[Reservation]:
    query = select(Reservation).where(Reservation.tenant_id == tenant_id)
    if hub_id:
        query = query.where(Reservation.hub_id == hub_id)
    if status:
        query = query.where(Reservation.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())


async def confirm_reservation(db: AsyncSession, tenant_id: str, reservation_id: uuid.UUID) -> Reservation:
    """Fully autonomous allocation — the first available vehicle
    matching hub+class is assigned in this same call, no human picks
    which physical unit a renter gets."""
    reservation = await get_reservation(db, tenant_id, reservation_id)
    if reservation is None:
        raise ValueError(f"reservation {reservation_id} not found")

    result = await db.execute(
        select(RentalVehicle).where(
            RentalVehicle.tenant_id == tenant_id, RentalVehicle.hub_id == reservation.hub_id,
            RentalVehicle.rental_class == reservation.rental_class, RentalVehicle.status == VehicleStatus.AVAILABLE,
        )
    )
    vehicle = result.scalars().first()
    if vehicle is None:
        raise NoVehicleAvailableError(f"no available {reservation.rental_class.value} vehicle at hub {reservation.hub_id}")

    reservation.vehicle_id = vehicle.id
    reservation.status = ReservationStatus.CONFIRMED
    await db.commit()
    await db.refresh(reservation)
    return reservation


async def activate_reservation(db: AsyncSession, tenant_id: str, reservation_id: uuid.UUID) -> Reservation:
    reservation = await get_reservation(db, tenant_id, reservation_id)
    if reservation is None:
        raise ValueError(f"reservation {reservation_id} not found")
    reservation.status = ReservationStatus.ACTIVE
    if reservation.vehicle_id:
        vehicle_result = await db.execute(select(RentalVehicle).where(RentalVehicle.id == reservation.vehicle_id))
        vehicle = vehicle_result.scalar_one_or_none()
        if vehicle:
            vehicle.status = VehicleStatus.RENTED
    await db.commit()
    await db.refresh(reservation)
    return reservation


async def complete_reservation(db: AsyncSession, tenant_id: str, reservation_id: uuid.UUID) -> Reservation:
    reservation = await get_reservation(db, tenant_id, reservation_id)
    if reservation is None:
        raise ValueError(f"reservation {reservation_id} not found")
    reservation.status = ReservationStatus.COMPLETED
    if reservation.vehicle_id:
        vehicle_result = await db.execute(select(RentalVehicle).where(RentalVehicle.id == reservation.vehicle_id))
        vehicle = vehicle_result.scalar_one_or_none()
        if vehicle:
            vehicle.status = VehicleStatus.AVAILABLE
    await db.commit()
    await db.refresh(reservation)
    return reservation


async def cancel_reservation(db: AsyncSession, tenant_id: str, reservation_id: uuid.UUID) -> Reservation:
    reservation = await get_reservation(db, tenant_id, reservation_id)
    if reservation is None:
        raise ValueError(f"reservation {reservation_id} not found")
    reservation.status = ReservationStatus.CANCELLED
    if reservation.vehicle_id:
        vehicle_result = await db.execute(select(RentalVehicle).where(RentalVehicle.id == reservation.vehicle_id))
        vehicle = vehicle_result.scalar_one_or_none()
        if vehicle:
            vehicle.status = VehicleStatus.AVAILABLE
    db.add(BookingEvent(tenant_id=tenant_id, hub_id=reservation.hub_id, rental_class=reservation.rental_class, event_type=BookingEventType.CANCELLED))
    await db.commit()
    await db.refresh(reservation)
    return reservation


async def modify_reservation(db: AsyncSession, tenant_id: str, reservation_id: uuid.UUID, new_daily_rate_usd: float) -> Reservation:
    """The one structural rule in this engine: a rate change is
    refused outright — raised, not silently ignored — once the
    reservation has reached ACTIVE or COMPLETED. A renter who has
    already taken the vehicle can never have their price retroactively
    altered."""
    reservation = await get_reservation(db, tenant_id, reservation_id)
    if reservation is None:
        raise ValueError(f"reservation {reservation_id} not found")
    if reservation.status in (ReservationStatus.ACTIVE, ReservationStatus.COMPLETED):
        raise ReservationLockedError(f"reservation is {reservation.status.value} — the rate can only be modified before checkout")

    reservation.daily_rate_usd = new_daily_rate_usd
    await db.commit()
    await db.refresh(reservation)
    return reservation
