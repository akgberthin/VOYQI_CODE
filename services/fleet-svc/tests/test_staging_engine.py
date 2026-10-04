import uuid

from src.models import VehicleClass, VehicleStatus
from src.services import inventory_engine, staging_engine

TENANT = "voyqi-holdings"


async def _certified_vehicle(db_session, vin="1HGCM82633A004352", hub_id=None):
    po = await inventory_engine.create_purchase_order(
        db_session, tenant_id=TENANT, source="dealer", vehicle_description="test", cost_usd=50000.0, created_by="fleet-ops-1",
    )
    vehicle = await inventory_engine.receive_vehicle(
        db_session, tenant_id=TENANT, po_id=po.id, vin=vin, make="Test", model="Car", year=2024,
        vehicle_class=VehicleClass.EXECUTIVE, ownership_entity="x",
    )
    await inventory_engine.provision_vehicle(db_session, TENANT, vehicle.id, "fleet-ops-1", "VQ-1")
    await inventory_engine.certify_class(db_session, TENANT, vehicle.id, "fleet-ops-1")
    if hub_id:
        vehicle.current_hub_id = hub_id
        await db_session.commit()
        await db_session.refresh(vehicle)
    return vehicle


async def test_create_hub(db_session):
    hub = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Brickell", region="Miami")
    assert hub.name == "Brickell"


async def test_hub_supply_counts_by_class(db_session):
    hub = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Brickell")
    await _certified_vehicle(db_session, vin="1HGCM82633A004352", hub_id=hub.id)
    await _certified_vehicle(db_session, vin="1HGCM82633A004353", hub_id=hub.id)
    supply = await staging_engine.get_hub_supply(db_session, TENANT, hub.id)
    assert supply["executive"] == 2


async def test_propose_transfer(db_session):
    hub_a = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Brickell")
    hub_b = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Wynwood")
    vehicle = await _certified_vehicle(db_session, hub_id=hub_a.id)

    order = await staging_engine.propose_transfer(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, to_hub_id=hub_b.id,
        reason="Wynwood demand spike", ai_initiated=True, generated_by_agent_key="fleet_intelligence_agent",
    )
    assert order.status.value == "proposed"
    assert order.from_hub_id == hub_a.id
    assert order.ai_initiated is True


async def test_propose_transfer_unknown_vehicle_raises(db_session):
    import pytest

    with pytest.raises(ValueError):
        await staging_engine.propose_transfer(
            db_session, tenant_id=TENANT, vehicle_id=uuid.uuid4(), to_hub_id=uuid.uuid4(), reason="x",
        )


async def test_dispatch_and_complete_transfer(db_session):
    hub_a = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Brickell")
    hub_b = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Wynwood")
    vehicle = await _certified_vehicle(db_session, hub_id=hub_a.id)
    order = await staging_engine.propose_transfer(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, to_hub_id=hub_b.id, reason="demand",
    )

    dispatched = await staging_engine.dispatch_transfer(db_session, TENANT, order.id, "fleet-ops-1", "driver-42")
    assert dispatched.status.value == "dispatched"

    completed = await staging_engine.complete_transfer(db_session, TENANT, order.id)
    assert completed.status.value == "completed"

    updated_vehicle = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert updated_vehicle.current_hub_id == hub_b.id
    assert updated_vehicle.status == VehicleStatus.STAGED


async def test_cannot_dispatch_twice(db_session):
    hub_a = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Brickell")
    hub_b = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Wynwood")
    vehicle = await _certified_vehicle(db_session, hub_id=hub_a.id)
    order = await staging_engine.propose_transfer(db_session, TENANT, vehicle.id, hub_b.id, "demand")
    await staging_engine.dispatch_transfer(db_session, TENANT, order.id, "fleet-ops-1", "driver-1")
    second = await staging_engine.dispatch_transfer(db_session, TENANT, order.id, "fleet-ops-1", "driver-2")
    assert second is None


async def test_cancel_proposed_transfer(db_session):
    hub_a = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Brickell")
    hub_b = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Wynwood")
    vehicle = await _certified_vehicle(db_session, hub_id=hub_a.id)
    order = await staging_engine.propose_transfer(db_session, TENANT, vehicle.id, hub_b.id, "demand")
    cancelled = await staging_engine.cancel_transfer(db_session, TENANT, order.id)
    assert cancelled.status.value == "cancelled"


async def test_list_transfers_filtered_by_status(db_session):
    hub_a = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Brickell")
    hub_b = await staging_engine.create_hub(db_session, tenant_id=TENANT, name="Wynwood")
    v1 = await _certified_vehicle(db_session, vin="1HGCM82633A004352", hub_id=hub_a.id)
    v2 = await _certified_vehicle(db_session, vin="1HGCM82633A004353", hub_id=hub_a.id)
    await staging_engine.propose_transfer(db_session, TENANT, v1.id, hub_b.id, "demand")
    order2 = await staging_engine.propose_transfer(db_session, TENANT, v2.id, hub_b.id, "demand")
    await staging_engine.dispatch_transfer(db_session, TENANT, order2.id, "fleet-ops-1", "driver-1")

    from src.models import TransferOrderStatus

    proposed = await staging_engine.list_transfers(db_session, TENANT, status=TransferOrderStatus.PROPOSED)
    assert len(proposed) == 1
