"""Device telemetry over MQTT: topic → vehicle resolution, tenant checks,
server-side geofencing on crossing out of the hub, tamper events."""
import json

import pytest

from src.core import kafka_events
from src.models import GeofenceAlert, TamperEvent, TelemetryReading, Vehicle, VehicleClass
from src.services import mqtt_ingest, staging_engine

TENANT = "voyqi-holdings"
VIN = "1HGCM82633A004352"
AUSTIN = (30.2672, -97.7431)
DALLAS = (32.7767, -96.7970)  # ~180 miles from Austin


@pytest.fixture
def events(monkeypatch):
    published = []

    async def record(topic, event_type, tenant_id, payload):
        published.append((event_type, tenant_id, payload))

    monkeypatch.setattr(kafka_events, "publish_event", record)
    return published


@pytest.fixture
async def vehicle(db_session):
    hub = await staging_engine.create_hub(db_session, TENANT, "Austin", latitude=AUSTIN[0], longitude=AUSTIN[1], geofence_radius_miles=25)
    v = Vehicle(tenant_id=TENANT, vin=VIN, make="Tesla", model="Model 3", year=2025, vehicle_class=VehicleClass.URBAN, current_hub_id=hub.id)
    db_session.add(v)
    await db_session.commit()
    return v


def _telemetry(lat, lon, odometer=1000, **extra):
    return json.dumps({"latitude": lat, "longitude": lon, "battery_or_fuel_pct": 80, "odometer_miles": odometer, **extra}).encode()


async def _count(db, model):
    from sqlalchemy import func, select

    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


@pytest.mark.parametrize("topic", [
    "voyqi/t/vehicles/VIN", "other/t/vehicles/VIN/telemetry", "voyqi/t/cars/VIN/telemetry",
    "voyqi/t/vehicles/VIN/firmware", "voyqi//vehicles/VIN/telemetry",
])
def test_unrecognised_topics(topic):
    assert mqtt_ingest.parse_topic(topic) is None


def test_topic_round_trip():
    assert mqtt_ingest.parse_topic(mqtt_ingest.vehicle_topic(TENANT, VIN, "tamper")) == (TENANT, VIN, "tamper")


async def test_reading_is_stored_and_odometer_advanced(db_session, vehicle, events):
    result = await mqtt_ingest.handle_message(
        db_session, mqtt_ingest.vehicle_topic(TENANT, VIN, "telemetry"), _telemetry(*AUSTIN, odometer=1500, dtc_codes=["P0420"]),
    )
    assert result["status"] == "ingested"
    await db_session.refresh(vehicle)
    assert vehicle.odometer_miles == 1500
    assert await _count(db_session, TelemetryReading) == 1
    assert events == []  # routine readings publish nothing


async def test_another_tenant_cannot_report_for_a_vin(db_session, vehicle, events):
    result = await mqtt_ingest.handle_message(db_session, mqtt_ingest.vehicle_topic("other-tenant", VIN, "telemetry"), _telemetry(*AUSTIN))
    assert result == {"status": "rejected", "reason": "unknown vehicle for tenant"}
    assert await _count(db_session, TelemetryReading) == 0


async def test_unknown_vin_and_bad_payloads_are_dropped(db_session, vehicle, events):
    unknown = await mqtt_ingest.handle_message(db_session, mqtt_ingest.vehicle_topic(TENANT, "NOPE", "telemetry"), _telemetry(*AUSTIN))
    assert unknown["status"] == "rejected"
    for payload in (b"not json", b'{"latitude": 999, "longitude": 0, "odometer_miles": 1}', b'{"latitude": 1}'):
        result = await mqtt_ingest.handle_message(db_session, mqtt_ingest.vehicle_topic(TENANT, VIN, "telemetry"), payload)
        assert result == {"status": "rejected", "reason": "malformed payload"}
    assert await _count(db_session, TelemetryReading) == 0


async def test_geofence_alerts_once_on_leaving_the_hub(db_session, vehicle, events):
    topic = mqtt_ingest.vehicle_topic(TENANT, VIN, "telemetry")
    inside = await mqtt_ingest.handle_message(db_session, topic, _telemetry(*AUSTIN))
    assert "geofence_alert_id" not in inside

    left = await mqtt_ingest.handle_message(db_session, topic, _telemetry(*DALLAS, odometer=1200))
    assert "geofence_alert_id" in left
    assert [e[0] for e in events] == ["flt.telemetry_anomaly_requested"]
    assert events[0][2]["geofence_alert_id"] == left["geofence_alert_id"]
    assert events[0][2]["distance_miles"] > 25

    still_out = await mqtt_ingest.handle_message(db_session, topic, _telemetry(DALLAS[0] + 0.01, DALLAS[1], odometer=1201))
    assert "geofence_alert_id" not in still_out
    assert await _count(db_session, GeofenceAlert) == 1

    await mqtt_ingest.handle_message(db_session, topic, _telemetry(*AUSTIN, odometer=1400))
    again = await mqtt_ingest.handle_message(db_session, topic, _telemetry(*DALLAS, odometer=1600))
    assert "geofence_alert_id" in again
    assert await _count(db_session, GeofenceAlert) == 2


async def test_first_reading_outside_the_hub_alerts(db_session, vehicle, events):
    result = await mqtt_ingest.handle_message(db_session, mqtt_ingest.vehicle_topic(TENANT, VIN, "telemetry"), _telemetry(*DALLAS))
    assert "geofence_alert_id" in result


async def test_vehicle_without_located_hub_is_not_geofenced(db_session, vehicle, events):
    vehicle.current_hub_id = None
    await db_session.commit()
    result = await mqtt_ingest.handle_message(db_session, mqtt_ingest.vehicle_topic(TENANT, VIN, "telemetry"), _telemetry(*DALLAS))
    assert result["status"] == "ingested" and "geofence_alert_id" not in result
    assert events == []


async def test_tamper_signal_recorded_and_published_for_security_review(db_session, vehicle, events):
    result = await mqtt_ingest.handle_message(
        db_session, mqtt_ingest.vehicle_topic(TENANT, VIN, "tamper"), json.dumps({"detected_signal": "obd_unplugged"}).encode(),
    )
    assert result["status"] == "tamper_recorded"
    assert await _count(db_session, TamperEvent) == 1
    assert events == [("vehicle.tamper_detected", TENANT, {
        "event_id": result["event_id"], "vehicle_id": str(vehicle.id), "detected_signal": "obd_unplugged", "source": "mqtt",
    })]


async def test_hub_coordinates_round_trip_through_the_api(client):
    from tests.conftest import TENANT_HEADERS

    created = await client.post("/api/v1/fleet/hubs", json={"name": "Austin", "latitude": AUSTIN[0], "longitude": AUSTIN[1], "geofence_radius_miles": 30}, headers=TENANT_HEADERS)
    assert created.status_code == 201
    assert (created.json()["latitude"], created.json()["geofence_radius_miles"]) == (AUSTIN[0], 30)
    bad = await client.post("/api/v1/fleet/hubs", json={"name": "Nowhere", "latitude": 123}, headers=TENANT_HEADERS)
    assert bad.status_code == 422
