import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import reservation_engine

router = APIRouter(prefix="/api/v1/rental/reservations", tags=["reservations"])


class ReservationCreate(BaseModel):
    hub_id: str
    rental_class: str
    customer_name: str
    start_date: datetime
    end_date: datetime
    daily_rate_usd: float


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    hub_id: str
    rental_class: str
    customer_name: str
    vehicle_id: uuid.UUID | None
    start_date: datetime
    end_date: datetime
    daily_rate_usd: float
    status: str


@router.post("", response_model=ReservationOut, status_code=201)
async def create_reservation(body: ReservationCreate, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: records the real demand signal (a
    BookingEvent) in the same call the reservation is created in.
    Publishes rental.booked, which the Pricing Intelligence Agent is
    routed on to re-evaluate the hub/class rate."""
    reservation = await reservation_engine.create_reservation(db, tenant_id, body.hub_id, body.rental_class, body.customer_name, body.start_date, body.end_date, body.daily_rate_usd)
    background_tasks.add_task(
        publish_event, "rental.reservations", "rental.booked", tenant_id,
        {"reservation_id": str(reservation.id), "hub_id": body.hub_id, "rental_class": body.rental_class, "daily_rate_usd": body.daily_rate_usd},
    )
    return reservation


@router.get("", response_model=list[ReservationOut])
async def list_reservations(hub_id: str | None = None, status: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await reservation_engine.list_reservations(db, tenant_id, hub_id, status)


@router.get("/{reservation_id}", response_model=ReservationOut)
async def get_reservation(reservation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The exact lookup rental_tools.get_reservation calls — daily_rate_usd
    and status are what the Pricing Intelligence Agent reads before
    recommending an adjustment."""
    reservation = await reservation_engine.get_reservation(db, tenant_id, reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return reservation


@router.post("/{reservation_id}/confirm", response_model=ReservationOut)
async def confirm_reservation(reservation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous vehicle allocation — no human picks which
    physical unit a renter gets."""
    try:
        return await reservation_engine.confirm_reservation(db, tenant_id, reservation_id)
    except reservation_engine.NoVehicleAvailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{reservation_id}/activate", response_model=ReservationOut)
async def activate_reservation(reservation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await reservation_engine.activate_reservation(db, tenant_id, reservation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{reservation_id}/complete", response_model=ReservationOut)
async def complete_reservation(reservation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await reservation_engine.complete_reservation(db, tenant_id, reservation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{reservation_id}/cancel", response_model=ReservationOut)
async def cancel_reservation(reservation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await reservation_engine.cancel_reservation(db, tenant_id, reservation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ModifyRequest(BaseModel):
    daily_rate_usd: float


@router.post("/{reservation_id}/modify", response_model=ReservationOut)
async def modify_reservation(reservation_id: uuid.UUID, body: ModifyRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The exact endpoint rental_tools.apply_dynamic_rate calls.
    Refuses (409) once the reservation has reached ACTIVE or
    COMPLETED — a renter who has already taken the vehicle can never
    have their price retroactively altered."""
    try:
        return await reservation_engine.modify_reservation(db, tenant_id, reservation_id, body.daily_rate_usd)
    except reservation_engine.ReservationLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
