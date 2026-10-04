import uuid

from src.models import VehicleClass
from src.services import inventory_engine, telematics_engine

TENANT = "voyqi-holdings"


async def _certified_vehicle(db_session, vin="1HGCM82633A004352"):
    po = await inventory_engine.create_purchase_order(
        db_session, tenant_id=TENANT, source="dealer", vehicle_description="test", cost_usd=50000.0, created_by="fleet-ops-1",
    )
    vehicle = await inventory_engine.receive_vehicle(
        db_session, tenant_id=TENANT, po_id=po.id, vin=vin, make="Test", model="Car", year=2024,
        vehicle_class=VehicleClass.EXECUTIVE, ownership_entity="x",
    )
    return vehicle


def test_haversine_zero_distance():
    assert telematics_engine.haversine_miles(25.7617, -80.1918, 25.7617, -80.1918) == 0


def test_haversine_known_distance_approx():
    # Miami to Orlando is roughly 200 miles
    distance = telematics_engine.haversine_miles(25.7617, -80.1918, 28.5383, -81.3792)
    assert 180 < distance < 230


async def test_ingest_telemetry_updates_odometer(db_session):
    vehicle = await _certified_vehicle(db_session)
    await telematics_engine.ingest_telemetry(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, latitude=25.76, longitude=-80.19,
        battery_or_fuel_pct=80.0, odometer_miles=5000,
    )
    updated = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert updated.odometer_miles == 5000


async def test_ingest_telemetry_does_not_decrease_odometer(db_session):
    vehicle = await _certified_vehicle(db_session)
    await telematics_engine.ingest_telemetry(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, latitude=25.76, longitude=-80.19,
        battery_or_fuel_pct=80.0, odometer_miles=5000,
    )
    await telematics_engine.ingest_telemetry(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, latitude=25.76, longitude=-80.19,
        battery_or_fuel_pct=79.0, odometer_miles=4900,  # stale/out-of-order reading
    )
    updated = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert updated.odometer_miles == 5000


async def test_geofence_within_radius_no_alert(db_session):
    vehicle = await _certified_vehicle(db_session)
    alert = await telematics_engine.check_geofence(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, hub_id=uuid.uuid4(),
        vehicle_lat=25.76, vehicle_lon=-80.19, hub_lat=25.77, hub_lon=-80.20,
    )
    assert alert is None


async def test_geofence_beyond_radius_creates_alert(db_session):
    vehicle = await _certified_vehicle(db_session)
    alert = await telematics_engine.check_geofence(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, hub_id=uuid.uuid4(),
        vehicle_lat=25.76, vehicle_lon=-80.19, hub_lat=40.71, hub_lon=-74.01,  # NYC — way outside 50mi
    )
    assert alert is not None
    assert alert.distance_miles > 50


async def test_record_tamper_event(db_session):
    vehicle = await _certified_vehicle(db_session)
    event = await telematics_engine.record_tamper_event(db_session, TENANT, vehicle.id, "unexpected cert revocation attempt")
    assert event.detected_signal == "unexpected cert revocation attempt"


async def test_get_latest_reading_returns_most_recent(db_session):
    vehicle = await _certified_vehicle(db_session)
    await telematics_engine.ingest_telemetry(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, latitude=25.76, longitude=-80.19,
        battery_or_fuel_pct=80.0, odometer_miles=5000,
    )
    await telematics_engine.ingest_telemetry(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, latitude=25.77, longitude=-80.20,
        battery_or_fuel_pct=78.0, odometer_miles=5010,
    )
    latest = await telematics_engine.get_latest_reading(db_session, TENANT, vehicle.id)
    assert latest.odometer_miles == 5010


async def test_record_and_get_telemetry_anomaly(db_session):
    from src.models import TelemetryAnomalyLevel

    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004360")
    alert = await telematics_engine.check_geofence(db_session, TENANT, vehicle.id, uuid.uuid4(), 40.0, -74.0, 25.0, -80.0)  # NY to FL, way over 50mi

    await telematics_engine.record_telemetry_anomaly(db_session, TENANT, alert.id, TelemetryAnomalyLevel.NEEDS_INVESTIGATION, "large excursion")

    latest = await telematics_engine.get_telemetry_anomaly(db_session, TENANT, alert.id)
    assert latest.assessment.value == "needs_investigation"


async def test_get_telemetry_anomaly_no_assessment_returns_none(db_session):
    result = await telematics_engine.get_telemetry_anomaly(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_get_geofence_alert(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004361")
    alert = await telematics_engine.check_geofence(db_session, TENANT, vehicle.id, uuid.uuid4(), 40.0, -74.0, 25.0, -80.0)

    fetched = await telematics_engine.get_geofence_alert(db_session, TENANT, alert.id)
    assert fetched.id == alert.id


async def test_get_geofence_alert_unknown_returns_none(db_session):
    result = await telematics_engine.get_geofence_alert(db_session, TENANT, uuid.uuid4())
    assert result is None
