"""
FLT-002 device ingest over MQTT — the path real telematics units use.

Devices publish to

    voyqi/{tenant_id}/vehicles/{vin}/telemetry   {"latitude", "longitude", "battery_or_fuel_pct", "odometer_miles", "dtc_codes"}
    voyqi/{tenant_id}/vehicles/{vin}/tamper      {"detected_signal"}

In production each device holds a TLS client cert with its VIN as the
Common Name (see ProvisioningRecord) and the broker ACL only lets it
write under its own VIN, so the VIN in the topic is authenticated by the
broker. The tenant segment is not trusted: the VIN is resolved here and
a message whose tenant doesn't own that VIN is dropped.

Geofencing happens server-side against the vehicle's current hub: an
alert is raised when a vehicle crosses out of the hub's radius, not on
every reading while it stays outside, and the alert goes to the same
AI anomaly assessment as the HTTP path. Tamper signals are recorded and
published for human security review exactly as POST /tamper-events does.
"""
import json
import logging

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import kafka_events
from src.models import Hub, Vehicle
from src.services import telematics_engine

logger = logging.getLogger("fleet-svc.mqtt")

TOPIC_ROOT = "voyqi"


class DeviceTelemetry(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    battery_or_fuel_pct: float = Field(default=100.0, ge=0, le=100)
    odometer_miles: int = Field(ge=0)
    dtc_codes: list[str] = []


class DeviceTamper(BaseModel):
    detected_signal: str = Field(min_length=1, max_length=256)


def parse_topic(topic: str) -> tuple[str, str, str] | None:
    """(tenant_id, vin, kind) for voyqi/{tenant}/vehicles/{vin}/{kind}."""
    parts = topic.split("/")
    if len(parts) != 5 or parts[0] != TOPIC_ROOT or parts[2] != "vehicles":
        return None
    tenant_id, vin, kind = parts[1], parts[3], parts[4]
    if not tenant_id or not vin or kind not in ("telemetry", "tamper"):
        return None
    return tenant_id, vin, kind


async def _hub_distance(db: AsyncSession, vehicle: Vehicle, lat: float, lon: float) -> tuple[Hub, float, float] | None:
    if vehicle.current_hub_id is None:
        return None
    hub = (await db.execute(select(Hub).where(Hub.id == vehicle.current_hub_id, Hub.tenant_id == vehicle.tenant_id))).scalar_one_or_none()
    if hub is None or hub.latitude is None or hub.longitude is None:
        return None
    radius = hub.geofence_radius_miles or telematics_engine.GEOFENCE_RADIUS_MILES
    return hub, telematics_engine.haversine_miles(lat, lon, hub.latitude, hub.longitude), radius


async def handle_message(db: AsyncSession, topic: str, payload: bytes) -> dict:
    parsed = parse_topic(topic)
    if parsed is None:
        return {"status": "rejected", "reason": "unrecognised topic"}
    tenant_id, vin, kind = parsed

    vehicle = (await db.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
    if vehicle is None or vehicle.tenant_id != tenant_id:
        logger.warning("dropping %s message for VIN %s: not a vehicle of tenant %s", kind, vin, tenant_id)
        return {"status": "rejected", "reason": "unknown vehicle for tenant"}

    try:
        body = json.loads(payload)
        message = (DeviceTelemetry if kind == "telemetry" else DeviceTamper).model_validate(body)
    except (ValueError, ValidationError) as exc:
        logger.warning("dropping malformed %s message for VIN %s: %s", kind, vin, exc)
        return {"status": "rejected", "reason": "malformed payload"}

    if kind == "tamper":
        event = await telematics_engine.record_tamper_event(db, tenant_id, vehicle.id, message.detected_signal)
        await kafka_events.publish_event(
            "fleet.telematics", "vehicle.tamper_detected", tenant_id,
            {"event_id": str(event.id), "vehicle_id": str(vehicle.id), "detected_signal": message.detected_signal, "source": "mqtt"},
        )
        return {"status": "tamper_recorded", "event_id": str(event.id)}

    previous = await telematics_engine.get_latest_reading(db, tenant_id, vehicle.id)
    reading = await telematics_engine.ingest_telemetry(
        db, tenant_id=tenant_id, vehicle_id=vehicle.id, latitude=message.latitude, longitude=message.longitude,
        battery_or_fuel_pct=message.battery_or_fuel_pct, odometer_miles=message.odometer_miles, dtc_codes=message.dtc_codes,
    )
    result: dict = {"status": "ingested", "reading_id": str(reading.id)}

    fence = await _hub_distance(db, vehicle, message.latitude, message.longitude)
    if fence is None:
        return result
    hub, distance, radius = fence
    was_outside = previous is not None and telematics_engine.haversine_miles(previous.latitude, previous.longitude, hub.latitude, hub.longitude) > radius
    if distance > radius and not was_outside:
        alert = await telematics_engine.record_geofence_alert(db, tenant_id, vehicle.id, hub.id, distance)
        await kafka_events.publish_event(
            "fleet.telematics", "flt.telemetry_anomaly_requested", tenant_id,
            {"geofence_alert_id": str(alert.id), "distance_miles": alert.distance_miles},
        )
        result["geofence_alert_id"] = str(alert.id)
    return result


def vehicle_topic(tenant_id: str, vin: str, kind: str) -> str:
    return f"{TOPIC_ROOT}/{tenant_id}/vehicles/{vin}/{kind}"

