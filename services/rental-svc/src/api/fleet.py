import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import fleet_engine

router = APIRouter(prefix="/api/v1/rental/vehicles", tags=["fleet"])


class VehicleCreate(BaseModel):
    hub_id: str
    rental_class: str
    vin: str
    base_daily_rate_usd: float


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    hub_id: str
    rental_class: str
    vin: str
    base_daily_rate_usd: float
    status: str


@router.post("", response_model=VehicleOut, status_code=201)
async def create_vehicle(body: VehicleCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await fleet_engine.create_vehicle(db, tenant_id, body.hub_id, body.rental_class, body.vin, body.base_daily_rate_usd)


@router.get("", response_model=list[VehicleOut])
async def list_vehicles(hub_id: str | None = None, rental_class: str | None = None, status: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await fleet_engine.list_vehicles(db, tenant_id, hub_id, rental_class, status)


@router.get("/{vehicle_id}", response_model=VehicleOut)
async def get_vehicle(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    vehicle = await fleet_engine.get_vehicle(db, tenant_id, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


class StatusUpdate(BaseModel):
    status: str


@router.patch("/{vehicle_id}/status", response_model=VehicleOut)
async def set_vehicle_status(vehicle_id: uuid.UUID, body: StatusUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    vehicle = await fleet_engine.set_vehicle_status(db, tenant_id, vehicle_id, body.status)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle
