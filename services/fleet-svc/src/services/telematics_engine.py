"""
FLT-002 Telematics & IoT Monitor engine.

Geofencing uses a plain haversine distance calculation — no external
geo library dependency needed for a single-point distance check.
Tamper events are the one thing here that gets published for the
Compliance/Audit Agent; routine telemetry ingestion does not.
"""
import math
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    GeofenceAlert,
    TamperEvent,
    TelemetryAnomalyAssessment,
    TelemetryAnomalyLevel,
    TelemetryReading,
    Vehicle,
    VehicleStatus,
)

EARTH_RADIUS_MILES = 3958.8
GEOFENCE_RADIUS_MILES = 50.0


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


async def ingest_telemetry(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, latitude: float, longitude: float,
    battery_or_fuel_pct: float, odometer_miles: int, dtc_codes: list[str] | None = None,
) -> TelemetryReading:
    reading = TelemetryReading(
        tenant_id=tenant_id, vehicle_id=vehicle_id, latitude=latitude, longitude=longitude,
        battery_or_fuel_pct=battery_or_fuel_pct, odometer_miles=odometer_miles, dtc_codes=dtc_codes or [],
    )
    db.add(reading)

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle and odometer_miles > vehicle.odometer_miles:
        vehicle.odometer_miles = odometer_miles

    await db.commit()
    await db.refresh(reading)
    return reading


async def check_geofence(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, hub_id: uuid.UUID,
    vehicle_lat: float, vehicle_lon: float, hub_lat: float, hub_lon: float,
) -> GeofenceAlert | None:
    distance = haversine_miles(vehicle_lat, vehicle_lon, hub_lat, hub_lon)
    if distance <= GEOFENCE_RADIUS_MILES:
        return None
    alert = GeofenceAlert(tenant_id=tenant_id, vehicle_id=vehicle_id, hub_id=hub_id, distance_miles=distance)
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


async def record_tamper_event(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, detected_signal: str) -> TamperEvent:
    event = TamperEvent(tenant_id=tenant_id, vehicle_id=vehicle_id, detected_signal=detected_signal)
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def get_latest_reading(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> TelemetryReading | None:
    result = await db.execute(
        select(TelemetryReading)
        .where(TelemetryReading.tenant_id == tenant_id, TelemetryReading.vehicle_id == vehicle_id)
        .order_by(TelemetryReading.recorded_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_geofence_alert(db: AsyncSession, tenant_id: str, alert_id: uuid.UUID) -> GeofenceAlert | None:
    result = await db.execute(select(GeofenceAlert).where(GeofenceAlert.id == alert_id, GeofenceAlert.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def record_telemetry_anomaly(
    db: AsyncSession, tenant_id: str, geofence_alert_id: uuid.UUID, assessment: TelemetryAnomalyLevel, reasoning: str,
) -> TelemetryAnomalyAssessment:
    record = TelemetryAnomalyAssessment(tenant_id=tenant_id, geofence_alert_id=geofence_alert_id, assessment=assessment, reasoning=reasoning)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def auto_execute_telemetry_anomaly(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, assessment: TelemetryAnomalyLevel, agent_key: str,
) -> Vehicle | None:
    """Fully autonomous FLT-002 execution: NEEDS_INVESTIGATION pulls
    the vehicle from active service immediately (status -> MAINTENANCE)
    and sets flagged_for_investigation=True, rather than sitting as an
    advisory-only note. BENIGN takes no action. Tamper events are a
    categorically different signal and are never routed through this
    function at all — they always go straight to human security
    review through their own separate path."""
    if assessment != TelemetryAnomalyLevel.NEEDS_INVESTIGATION:
        return None
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        return None
    vehicle.flagged_for_investigation = True
    vehicle.status = VehicleStatus.MAINTENANCE
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def get_telemetry_anomaly(db: AsyncSession, tenant_id: str, geofence_alert_id: uuid.UUID) -> TelemetryAnomalyAssessment | None:
    result = await db.execute(
        select(TelemetryAnomalyAssessment).where(TelemetryAnomalyAssessment.tenant_id == tenant_id, TelemetryAnomalyAssessment.geofence_alert_id == geofence_alert_id)
        .order_by(TelemetryAnomalyAssessment.computed_at.desc())
    )
    return result.scalars().first()
