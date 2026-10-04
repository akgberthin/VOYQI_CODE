import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import EnergyType
from src.services import fuel_engine

router = APIRouter(prefix="/api/v1/fleet", tags=["fuel-energy"])


class FuelEntryCreate(BaseModel):
    vehicle_id: uuid.UUID
    energy_type: EnergyType
    quantity: float
    cost_usd: float
    odometer_miles: int


class FuelEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    energy_type: EnergyType
    quantity: float
    cost_usd: float
    odometer_miles: int
    logged_at: datetime


@router.post("/fuel-entries", response_model=FuelEntryOut, status_code=201)
async def log_fuel_entry(
    body: FuelEntryCreate, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    entry = await fuel_engine.log_fuel_entry(db, tenant_id, body.vehicle_id, body.energy_type, body.quantity, body.cost_usd, body.odometer_miles)
    # AI-first: every fuel entry gets an anomaly check automatically,
    # once there's a prior entry to compare against — a single fill
    # has no "implied efficiency" to evaluate. Neither the entry nor
    # the deterministic cost/efficiency math is ever touched by this.
    entries = await fuel_engine.list_fuel_entries(db, tenant_id, body.vehicle_id)
    if len(entries) >= 2:
        previous_entry = entries[-2]
        implied_efficiency = fuel_engine.compute_implied_efficiency(previous_entry, entry)
        cost_per_unit = round(entry.cost_usd / entry.quantity, 4) if entry.quantity > 0 else None
        background_tasks.add_task(
            publish_event,
            "fleet.fuel",
            "flt.fuel_anomaly_requested",
            tenant_id,
            {"vehicle_id": str(body.vehicle_id), "fuel_log_entry_id": str(entry.id), "energy_type": body.energy_type.value, "implied_efficiency": implied_efficiency, "cost_per_unit_usd": cost_per_unit},
        )
    return entry


@router.post("/fuel-entries/{entry_id}/assess-anomaly-now")
async def assess_fuel_anomaly_now(entry_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation fuel entry
    logging already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    entry = await fuel_engine.get_fuel_entry(db, tenant_id, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Fuel entry not found")
    entries = await fuel_engine.list_fuel_entries(db, tenant_id, entry.vehicle_id)
    idx = next((i for i, e in enumerate(entries) if e.id == entry_id), None)
    if idx is None or idx == 0:
        raise HTTPException(status_code=409, detail="No prior entry to compare against")
    implied_efficiency = fuel_engine.compute_implied_efficiency(entries[idx - 1], entry)
    cost_per_unit = round(entry.cost_usd / entry.quantity, 4) if entry.quantity > 0 else None
    response = ai_orchestration_tools.request_fuel_anomaly(tenant_id, str(entry.vehicle_id), str(entry_id), entry.energy_type.value, implied_efficiency, cost_per_unit)
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response


class FuelAnomalyCreate(BaseModel):
    vehicle_id: uuid.UUID
    assessment: str
    reasoning: str = ""


@router.post("/fuel-entries/{entry_id}/anomaly-assessments")
async def create_fuel_anomaly(entry_id: uuid.UUID, body: FuelAnomalyCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The agent's FLT-011 tool call — recording the anomaly and
    autonomously opening a maintenance alert on FLAGGED (chained
    straight into FLT-004) happen in the same call."""
    from src.models import FuelAnomalyLevel

    level = FuelAnomalyLevel(body.assessment)
    record = await fuel_engine.record_fuel_anomaly(db, tenant_id, body.vehicle_id, entry_id, level, body.reasoning)
    alert = await fuel_engine.auto_execute_fuel_anomaly(db, tenant_id, body.vehicle_id, level, body.reasoning)
    return {"id": str(record.id), "assessment": record.assessment.value, "auto_maintenance_alert_id": str(alert.id) if alert else None}


@router.get("/fuel-entries/{entry_id}/anomaly-assessment")
async def get_fuel_anomaly(entry_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    record = await fuel_engine.get_fuel_anomaly(db, tenant_id, entry_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No anomaly assessment on file")
    return {"id": str(record.id), "assessment": record.assessment.value, "reasoning": record.reasoning}


@router.get("/vehicles/{vehicle_id}/fuel-entries", response_model=list[FuelEntryOut])
async def list_fuel_entries(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await fuel_engine.list_fuel_entries(db, tenant_id, vehicle_id)


@router.get("/vehicles/{vehicle_id}/fuel-summary")
async def get_fuel_summary(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await fuel_engine.get_fuel_summary(db, tenant_id, vehicle_id)
