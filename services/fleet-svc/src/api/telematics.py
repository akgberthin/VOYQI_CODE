import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import TelemetryAnomalyLevel
from src.services import telematics_engine

router = APIRouter(prefix="/api/v1/fleet", tags=["telematics"])


class TelemetryIngest(BaseModel):
    vehicle_id: uuid.UUID
    latitude: float
    longitude: float
    battery_or_fuel_pct: float = 100.0
    odometer_miles: int
    dtc_codes: list[str] = []


class ReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    latitude: float
    longitude: float
    battery_or_fuel_pct: float
    dtc_codes: list[str]
    odometer_miles: int
    recorded_at: datetime


class GeofenceCheck(BaseModel):
    vehicle_id: uuid.UUID
    hub_id: uuid.UUID
    vehicle_lat: float
    vehicle_lon: float
    hub_lat: float
    hub_lon: float


class TamperReport(BaseModel):
    vehicle_id: uuid.UUID
    detected_signal: str


@router.post("/telemetry", response_model=ReadingOut, status_code=201)
async def ingest_telemetry(body: TelemetryIngest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await telematics_engine.ingest_telemetry(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, latitude=body.latitude, longitude=body.longitude,
        battery_or_fuel_pct=body.battery_or_fuel_pct, odometer_miles=body.odometer_miles, dtc_codes=body.dtc_codes,
    )


class FleetPosition(BaseModel):
    vehicle_id: uuid.UUID
    vin: str
    make: str
    model: str
    status: str
    current_hub_id: uuid.UUID | None
    latitude: float
    longitude: float
    battery_or_fuel_pct: float
    odometer_miles: int
    dtc_codes: list[str]
    recorded_at: datetime


@router.get("/telemetry/latest", response_model=list[FleetPosition])
async def fleet_positions(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Every reporting vehicle's latest reading, for the live fleet map."""
    return [
        FleetPosition(
            vehicle_id=v.id, vin=v.vin, make=v.make, model=v.model, status=v.status.value, current_hub_id=v.current_hub_id,
            latitude=r.latitude, longitude=r.longitude, battery_or_fuel_pct=r.battery_or_fuel_pct,
            odometer_miles=r.odometer_miles, dtc_codes=r.dtc_codes, recorded_at=r.recorded_at,
        )
        for r, v in await telematics_engine.latest_readings(db, tenant_id)
    ]


@router.get("/vehicles/{vehicle_id}/latest-telemetry", response_model=ReadingOut | None)
async def get_latest_reading(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await telematics_engine.get_latest_reading(db, tenant_id, vehicle_id)


@router.post("/geofence-check")
async def check_geofence(
    body: GeofenceCheck, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    alert = await telematics_engine.check_geofence(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, hub_id=body.hub_id,
        vehicle_lat=body.vehicle_lat, vehicle_lon=body.vehicle_lon, hub_lat=body.hub_lat, hub_lon=body.hub_lon,
    )
    if alert is None:
        return {"alert_created": False}
    # AI-first: every geofence alert gets an anomaly assessment
    # automatically — never a tamper event, which always escalates to
    # human security review through its own deterministic path below
    # and is never routed through this AI judgment at all.
    background_tasks.add_task(
        publish_event,
        "fleet.telematics",
        "flt.telemetry_anomaly_requested",
        tenant_id,
        {"geofence_alert_id": str(alert.id), "distance_miles": alert.distance_miles},
    )
    return {"alert_created": True, "alert_id": str(alert.id), "distance_miles": alert.distance_miles}


@router.post("/geofence-alerts/{alert_id}/assess-anomaly-now")
async def assess_anomaly_now(alert_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation geofence
    checking already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    alert = await telematics_engine.get_geofence_alert(db, tenant_id, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Geofence alert not found")
    response = ai_orchestration_tools.request_telemetry_anomaly(tenant_id, str(alert_id), alert.distance_miles)
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response


class AnomalyAssessmentCreate(BaseModel):
    assessment: str
    reasoning: str = ""
    agent_key: str = "fleet_intelligence_agent"


@router.post("/geofence-alerts/{alert_id}/anomaly-assessments")
async def create_anomaly_assessment(alert_id: uuid.UUID, body: AnomalyAssessmentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The agent's FLT-002 tool call — recording the assessment and
    executing it (auto-pulling the vehicle from service on
    NEEDS_INVESTIGATION) happen in the same call, no human gate."""
    level = TelemetryAnomalyLevel(body.assessment)
    record = await telematics_engine.record_telemetry_anomaly(db, tenant_id, alert_id, level, body.reasoning)
    alert = await telematics_engine.get_geofence_alert(db, tenant_id, alert_id)
    executed = False
    if alert is not None:
        vehicle = await telematics_engine.auto_execute_telemetry_anomaly(db, tenant_id, alert.vehicle_id, level, body.agent_key)
        executed = vehicle is not None
    return {"id": str(record.id), "assessment": record.assessment.value, "executed": executed}


@router.get("/geofence-alerts/{alert_id}/anomaly-assessment")
async def get_anomaly_assessment(alert_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    record = await telematics_engine.get_telemetry_anomaly(db, tenant_id, alert_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No anomaly assessment on file")
    return {"id": str(record.id), "assessment": record.assessment.value, "reasoning": record.reasoning}


@router.post("/tamper-events", status_code=201)
async def record_tamper_event(
    body: TamperReport,
    background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    event = await telematics_engine.record_tamper_event(db, tenant_id, body.vehicle_id, body.detected_signal)
    background_tasks.add_task(
        publish_event,
        "fleet.telematics",
        "vehicle.tamper_detected",
        tenant_id,
        {"event_id": str(event.id), "vehicle_id": str(body.vehicle_id), "detected_signal": body.detected_signal},
    )
    return {"event_id": str(event.id)}
