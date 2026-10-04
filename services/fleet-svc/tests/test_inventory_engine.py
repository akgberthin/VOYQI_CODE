import pytest

from src.models import VehicleClass, VehicleStatus
from src.services import inventory_engine
from src.services.inventory_engine import NotProvisionedError

TENANT = "voyqi-holdings"


async def _full_intake(db_session, vin="1HGCM82633A004352"):
    po = await inventory_engine.create_purchase_order(
        db_session, tenant_id=TENANT, source="dealer", vehicle_description="2024 Mercedes S-Class",
        cost_usd=95000.0, created_by="fleet-ops-1",
    )
    vehicle = await inventory_engine.receive_vehicle(
        db_session, tenant_id=TENANT, po_id=po.id, vin=vin, make="Mercedes-Benz", model="S-Class",
        year=2024, vehicle_class=VehicleClass.LUXURY, ownership_entity="VOYQI Fleet Holdings LLC",
    )
    await inventory_engine.record_inspection(
        db_session, tenant_id=TENANT, po_id=po.id, checklist={"tires": "pass", "brakes": "pass"},
        passed=True, inspected_by="fleet-ops-1",
    )
    return po, vehicle


async def test_full_intake_flow(db_session):
    _po, vehicle = await _full_intake(db_session)
    assert vehicle.status == VehicleStatus.IN_TRANSIT
    assert vehicle.is_provisioned is False


async def test_receive_rejects_wrong_po_status(db_session):
    po, _vehicle = await _full_intake(db_session)
    # already received — trying again should fail
    result = await inventory_engine.receive_vehicle(
        db_session, tenant_id=TENANT, po_id=po.id, vin="1FA6P8TH8E5395342", make="Ford", model="Mustang",
        year=2024, vehicle_class=VehicleClass.PERFORMANCE, ownership_entity="x",
    )
    assert result is None


async def test_cannot_stage_before_provisioning(db_session):
    _po, vehicle = await _full_intake(db_session)
    with pytest.raises(NotProvisionedError):
        await inventory_engine.certify_class(db_session, TENANT, vehicle.id, "fleet-ops-1")


async def test_provision_and_certify(db_session):
    _po, vehicle = await _full_intake(db_session)
    record = await inventory_engine.provision_vehicle(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, provisioned_by="fleet-ops-1", card_id="VQ-1001",
    )
    assert record.tls_cert_common_name == vehicle.vin  # VIN as Common Name

    certified = await inventory_engine.certify_class(db_session, TENANT, vehicle.id, "fleet-ops-1")
    assert certified.is_class_certified is True
    assert certified.status == VehicleStatus.STAGED


async def test_uncertified_vehicle_cannot_be_assigned(db_session):
    _po, vehicle = await _full_intake(db_session)
    with pytest.raises(NotProvisionedError):
        await inventory_engine.update_vehicle_status(db_session, TENANT, vehicle.id, VehicleStatus.ASSIGNED)


async def test_certified_vehicle_can_be_assigned(db_session):
    _po, vehicle = await _full_intake(db_session)
    await inventory_engine.provision_vehicle(db_session, TENANT, vehicle.id, "fleet-ops-1", "VQ-1002")
    await inventory_engine.certify_class(db_session, TENANT, vehicle.id, "fleet-ops-1")

    updated = await inventory_engine.update_vehicle_status(db_session, TENANT, vehicle.id, VehicleStatus.ASSIGNED)
    assert updated.status == VehicleStatus.ASSIGNED


async def test_maintenance_status_does_not_require_certification(db_session):
    _po, vehicle = await _full_intake(db_session)
    # A brand-new, not-yet-certified vehicle can still go to maintenance
    # status (e.g. pre-delivery service) — the gate is specifically on
    # member-facing statuses.
    updated = await inventory_engine.update_vehicle_status(db_session, TENANT, vehicle.id, VehicleStatus.MAINTENANCE)
    assert updated.status == VehicleStatus.MAINTENANCE


async def test_search_vehicles_by_class(db_session):
    await _full_intake(db_session, vin="1HGCM82633A004352")
    po2 = await inventory_engine.create_purchase_order(
        db_session, tenant_id=TENANT, source="auction", vehicle_description="Honda Accord",
        cost_usd=25000.0, created_by="fleet-ops-1",
    )
    await inventory_engine.receive_vehicle(
        db_session, tenant_id=TENANT, po_id=po2.id, vin="1HGCV1F34LA000001", make="Honda", model="Accord",
        year=2024, vehicle_class=VehicleClass.URBAN, ownership_entity="x",
    )
    luxury_only = await inventory_engine.search_vehicles(db_session, TENANT, vehicle_class=VehicleClass.LUXURY)
    assert len(luxury_only) == 1
    assert luxury_only[0].make == "Mercedes-Benz"


async def test_record_and_get_intake_readiness(db_session):
    from sqlalchemy import select

    from src.models import IntakeInspection, IntakeReadinessRecommendation

    po, _vehicle = await _full_intake(db_session, vin="1HGCM82633A004353")
    result = await db_session.execute(select(IntakeInspection).where(IntakeInspection.purchase_order_id == po.id))
    inspection = result.scalar_one()

    await inventory_engine.record_intake_readiness(db_session, TENANT, inspection.id, IntakeReadinessRecommendation.PROVISION_WITH_FLAG, "minor cosmetic note found")

    latest = await inventory_engine.get_intake_readiness(db_session, TENANT, inspection.id)
    assert latest.recommendation.value == "provision_with_flag"


async def test_get_intake_readiness_no_assessment_returns_none(db_session):
    import uuid
    result = await inventory_engine.get_intake_readiness(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_get_intake_inspection(db_session):
    from sqlalchemy import select

    from src.models import IntakeInspection

    po, _vehicle = await _full_intake(db_session, vin="1HGCM82633A004354")
    result = await db_session.execute(select(IntakeInspection).where(IntakeInspection.purchase_order_id == po.id))
    inspection = result.scalar_one()

    fetched = await inventory_engine.get_intake_inspection(db_session, TENANT, inspection.id)
    assert fetched.id == inspection.id
    assert fetched.notes == ""
