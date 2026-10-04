import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import ChargingSessionStatus
from src.services import ev_charging_engine

router = APIRouter(prefix="/api/v1/fleet", tags=["ev-charging"])


class ChargingSessionStart(BaseModel):
    vehicle_id: uuid.UUID
    station_location: str = ""


class ChargingSessionComplete(BaseModel):
    energy_added_kwh: float
    cost_usd: float


class ChargingSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    station_location: str
    energy_added_kwh: float
    cost_usd: float
    status: ChargingSessionStatus
    started_at: datetime
    ended_at: datetime | None


class BatteryHealthCreate(BaseModel):
    vehicle_id: uuid.UUID
    state_of_charge_pct: float
    state_of_health_pct: float = 100.0


class BatteryHealthOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    state_of_charge_pct: float
    state_of_health_pct: float
    recorded_at: datetime


@router.post("/charging-sessions", response_model=ChargingSessionOut, status_code=201)
async def start_charging_session(body: ChargingSessionStart, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ev_charging_engine.start_charging_session(db, tenant_id, body.vehicle_id, body.station_location)


@router.post("/charging-sessions/{session_id}/complete", response_model=ChargingSessionOut)
async def complete_charging_session(session_id: uuid.UUID, body: ChargingSessionComplete, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    session = await ev_charging_engine.complete_charging_session(db, tenant_id, session_id, body.energy_added_kwh, body.cost_usd)
    if session is None:
        raise HTTPException(status_code=409, detail="Session not found or already completed")
    return session


@router.get("/vehicles/{vehicle_id}/charging-sessions", response_model=list[ChargingSessionOut])
async def list_charging_sessions(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ev_charging_engine.list_charging_sessions(db, tenant_id, vehicle_id)


@router.get("/vehicles/{vehicle_id}/charging-summary")
async def get_charging_cost_summary(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ev_charging_engine.get_charging_cost_summary(db, tenant_id, vehicle_id)


@router.post("/battery-health", response_model=BatteryHealthOut, status_code=201)
async def record_battery_health(
    body: BatteryHealthCreate, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    reading = await ev_charging_engine.record_battery_health(db, tenant_id, body.vehicle_id, body.state_of_charge_pct, body.state_of_health_pct)
    # AI-first: every reading gets a trend forecast automatically, once
    # there are at least 2 readings to compute a trend from. This never
    # touches is_degradation_alert or the watchlist — both remain the
    # deterministic single-reading threshold check they already were.
    readings = await ev_charging_engine.list_battery_health_readings(db, tenant_id, body.vehicle_id)
    if len(readings) >= 2:
        first, last = readings[0], readings[-1]
        days_elapsed = max((last.recorded_at - first.recorded_at).total_seconds() / 86400, 0.01)
        from src.core.config import get_settings

        settings = get_settings()
        background_tasks.add_task(
            publish_event,
            "fleet.ev",
            "flt.battery_degradation_trend_requested",
            tenant_id,
            {"vehicle_id": str(body.vehicle_id), "first_soh_pct": first.state_of_health_pct, "latest_soh_pct": last.state_of_health_pct, "days_elapsed": days_elapsed, "threshold_pct": settings.battery_health_alert_threshold_pct},
        )
    return reading


@router.post("/vehicles/{vehicle_id}/forecast-battery-degradation-now")
async def forecast_battery_degradation_now(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation battery
    health recording already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    readings = await ev_charging_engine.list_battery_health_readings(db, tenant_id, vehicle_id)
    if len(readings) < 2:
        raise HTTPException(status_code=409, detail="Need at least 2 battery health readings to compute a trend")
    first, last = readings[0], readings[-1]
    days_elapsed = max((last.recorded_at - first.recorded_at).total_seconds() / 86400, 0.01)
    from src.core.config import get_settings

    settings = get_settings()
    response = ai_orchestration_tools.request_battery_degradation_trend(tenant_id, str(vehicle_id), first.state_of_health_pct, last.state_of_health_pct, days_elapsed, settings.battery_health_alert_threshold_pct)
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response


class BatteryForecastCreate(BaseModel):
    degradation_rate_pct_per_day: float
    estimated_days_to_threshold: int | None = None
    reasoning: str = ""


@router.post("/vehicles/{vehicle_id}/battery-degradation-forecasts")
async def create_battery_degradation_forecast(vehicle_id: uuid.UUID, body: BatteryForecastCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The agent's FLT-012 tool call — recording the forecast and
    autonomously opening a maintenance alert when the projection is
    urgent (chained straight into FLT-004) happen in the same call."""
    forecast = await ev_charging_engine.record_battery_degradation_forecast(db, tenant_id, vehicle_id, body.degradation_rate_pct_per_day, body.estimated_days_to_threshold, body.reasoning)
    alert = await ev_charging_engine.auto_execute_battery_degradation_forecast(db, tenant_id, vehicle_id, body.estimated_days_to_threshold, body.reasoning)
    return {"id": str(forecast.id), "estimated_days_to_threshold": forecast.estimated_days_to_threshold, "auto_maintenance_alert_id": str(alert.id) if alert else None}


@router.get("/vehicles/{vehicle_id}/battery-degradation-forecast")
async def get_battery_degradation_forecast(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    forecast = await ev_charging_engine.get_latest_battery_degradation_forecast(db, tenant_id, vehicle_id)
    if forecast is None:
        raise HTTPException(status_code=404, detail="No degradation forecast on file")
    return {"id": str(forecast.id), "degradation_rate_pct_per_day": forecast.degradation_rate_pct_per_day, "estimated_days_to_threshold": forecast.estimated_days_to_threshold, "reasoning": forecast.reasoning}


@router.get("/vehicles/{vehicle_id}/battery-health/latest", response_model=BatteryHealthOut | None)
async def get_latest_battery_health(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ev_charging_engine.get_latest_battery_health(db, tenant_id, vehicle_id)


@router.get("/battery-health/watchlist", response_model=list[BatteryHealthOut])
async def get_battery_degradation_watchlist(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ev_charging_engine.list_battery_degradation_watchlist(db, tenant_id)
