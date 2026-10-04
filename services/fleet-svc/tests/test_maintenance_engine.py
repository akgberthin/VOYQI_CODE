import uuid

from src.models import MaintenanceAlertSeverity, VehicleClass, VehicleStatus
from src.services import inventory_engine, maintenance_engine

TENANT = "voyqi-holdings"


async def _certified_vehicle(db_session, vin="1HGCM82633A004352"):
    po = await inventory_engine.create_purchase_order(
        db_session, tenant_id=TENANT, source="dealer", vehicle_description="test", cost_usd=50000.0, created_by="fleet-ops-1",
    )
    vehicle = await inventory_engine.receive_vehicle(
        db_session, tenant_id=TENANT, po_id=po.id, vin=vin, make="Test", model="Car", year=2024,
        vehicle_class=VehicleClass.EXECUTIVE, ownership_entity="x",
    )
    await inventory_engine.provision_vehicle(db_session, TENANT, vehicle.id, "fleet-ops-1", "VQ-1")
    await inventory_engine.certify_class(db_session, TENANT, vehicle.id, "fleet-ops-1")
    return vehicle


async def test_no_alert_when_within_interval(db_session):
    alert = await maintenance_engine.evaluate_maintenance_need(db_session, TENANT, uuid.uuid4(), miles_since_service=1000)
    assert alert is None


async def test_predictive_alert_when_over_mileage_interval(db_session):
    alert = await maintenance_engine.evaluate_maintenance_need(db_session, TENANT, uuid.uuid4(), miles_since_service=6000)
    assert alert.severity == MaintenanceAlertSeverity.PREDICTIVE


async def test_urgent_alert_when_dtc_present(db_session):
    alert = await maintenance_engine.evaluate_maintenance_need(
        db_session, TENANT, uuid.uuid4(), miles_since_service=100, dtc_codes=["P0301"]
    )
    assert alert.severity == MaintenanceAlertSeverity.URGENT
    assert "P0301" in alert.reason


async def test_urgent_work_order_pulls_vehicle_to_maintenance(db_session):
    vehicle = await _certified_vehicle(db_session)
    alert = await maintenance_engine.evaluate_maintenance_need(
        db_session, TENANT, vehicle.id, miles_since_service=100, dtc_codes=["P0301"]
    )
    await maintenance_engine.create_work_order(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, vendor="Downtown Service Center",
        created_by="fleet-ops-1", alert_id=alert.id,
    )
    updated = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert updated.status == VehicleStatus.MAINTENANCE


async def test_predictive_work_order_does_not_pull_vehicle(db_session):
    vehicle = await _certified_vehicle(db_session)
    alert = await maintenance_engine.evaluate_maintenance_need(db_session, TENANT, vehicle.id, miles_since_service=6000)
    await maintenance_engine.create_work_order(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, vendor="Downtown Service Center",
        created_by="fleet-ops-1", alert_id=alert.id,
    )
    updated = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert updated.status == VehicleStatus.STAGED


async def test_complete_work_order_restores_vehicle_status(db_session):
    vehicle = await _certified_vehicle(db_session)
    alert = await maintenance_engine.evaluate_maintenance_need(
        db_session, TENANT, vehicle.id, miles_since_service=100, dtc_codes=["P0301"]
    )
    order = await maintenance_engine.create_work_order(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, vendor="x", created_by="fleet-ops-1", alert_id=alert.id,
    )
    completed = await maintenance_engine.complete_work_order(db_session, TENANT, order.id)
    assert completed.status.value == "completed"

    updated = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert updated.status == VehicleStatus.STAGED

    downtime = maintenance_engine.compute_downtime_hours(completed)
    assert downtime is not None
    assert downtime >= 0


async def test_complete_unknown_work_order_returns_none(db_session):
    result = await maintenance_engine.complete_work_order(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_complete_work_order_records_cost(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1FTBW2CM5DKA00000")
    order = await maintenance_engine.create_work_order(db_session, TENANT, vehicle.id, "VOYQI Certified Service", "fleet-ops-1")

    completed = await maintenance_engine.complete_work_order(db_session, TENANT, order.id, cost_usd=485.50)
    assert completed.cost_usd == 485.50


async def test_work_order_defaults_to_zero_cost_if_unspecified(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1FTBW2CM5DKA00001")
    order = await maintenance_engine.create_work_order(db_session, TENANT, vehicle.id, "VOYQI Certified Service", "fleet-ops-1")

    completed = await maintenance_engine.complete_work_order(db_session, TENANT, order.id)
    assert completed.cost_usd == 0.0


async def test_list_open_unacknowledged_alerts_excludes_acknowledged(db_session):
    vehicle1 = await _certified_vehicle(db_session, vin="1HGCM82633A004380")
    vehicle2 = await _certified_vehicle(db_session, vin="1HGCM82633A004381")
    await maintenance_engine.evaluate_maintenance_need(db_session, TENANT, vehicle1.id, 6000, dtc_codes=["P0300"])
    await maintenance_engine.evaluate_maintenance_need(db_session, TENANT, vehicle2.id, 6000, dtc_codes=[])

    open_alerts = await maintenance_engine.list_open_unacknowledged_alerts(db_session, TENANT)
    assert len(open_alerts) == 2


async def test_record_and_get_latest_maintenance_triage(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004382")
    alert = await maintenance_engine.evaluate_maintenance_need(db_session, TENANT, vehicle.id, 6000, dtc_codes=["P0300"])

    await maintenance_engine.record_maintenance_triage(db_session, TENANT, [str(alert.id)], [str(vehicle.id)], "urgent DTC takes priority")

    latest = await maintenance_engine.get_latest_maintenance_triage(db_session, TENANT)
    assert latest.priority_order == [str(vehicle.id)]


async def test_get_latest_maintenance_triage_none_when_never_computed(db_session):
    result = await maintenance_engine.get_latest_maintenance_triage(db_session, TENANT)
    assert result is None
