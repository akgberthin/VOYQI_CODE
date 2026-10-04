from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import demand_engine, utilization_engine

router = APIRouter(prefix="/api/v1/rental/analytics", tags=["analytics"])


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    hub_id: str
    rental_class: str
    booked_count: int
    total_count: int
    utilization_pct: float
    snapshot_at: datetime


@router.post("/snapshots", response_model=SnapshotOut, status_code=201)
async def record_snapshot(hub_id: str, rental_class: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await utilization_engine.record_snapshot(db, tenant_id, hub_id, rental_class)


@router.get("/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(hub_id: str | None = None, rental_class: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await utilization_engine.list_snapshots(db, tenant_id, hub_id, rental_class)


class ForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    hub_id: str
    rental_class: str
    booking_velocity_per_day: float
    trend: str
    forecasted_utilization_pct: float
    reasoning: str
    generated_at: datetime


@router.get("/forecasts", response_model=list[ForecastOut])
async def list_forecasts(hub_id: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await demand_engine.list_forecasts(db, tenant_id, hub_id)
