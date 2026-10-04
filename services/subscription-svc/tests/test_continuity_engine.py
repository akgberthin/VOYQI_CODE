import uuid

from src.models import MobilityClass
from src.services import continuity_engine, fleet_tools, plan_engine

TENANT = "voyqi-holdings"


async def test_declare_travel_resolves_equivalent_class_and_stages(db_session, monkeypatch):
    await plan_engine.create_class_equivalency(db_session, TENANT, MobilityClass.EXECUTIVE, "london", MobilityClass.LUXURY)
    fake_vehicle_id = str(uuid.uuid4())
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda tenant_id, vehicle_class, hub_id=None: [{"id": fake_vehicle_id}])
    monkeypatch.setattr(fleet_tools, "propose_transfer", lambda *a, **k: {"id": str(uuid.uuid4())})

    hub_id = str(uuid.uuid4())
    declaration = await continuity_engine.declare_travel(
        db_session, TENANT, uuid.uuid4(), MobilityClass.EXECUTIVE, "london", hub_id
    )
    assert declaration.resolved_equivalent_class == MobilityClass.LUXURY
    assert str(declaration.staged_vehicle_id) == fake_vehicle_id


async def test_declare_travel_without_availability_leaves_unstaged(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda *a, **k: [])
    declaration = await continuity_engine.declare_travel(
        db_session, TENANT, uuid.uuid4(), MobilityClass.URBAN, "unconfigured_market", str(uuid.uuid4())
    )
    assert declaration.staged_vehicle_id is None
    assert declaration.resolved_equivalent_class == MobilityClass.URBAN  # falls back, no rule configured


async def test_complete_travel(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda *a, **k: [])
    declaration = await continuity_engine.declare_travel(
        db_session, TENANT, uuid.uuid4(), MobilityClass.URBAN, "miami", str(uuid.uuid4())
    )
    completed = await continuity_engine.complete_travel(db_session, TENANT, declaration.id)
    assert completed.is_active is False
    assert completed.completed_at is not None


async def test_cannot_complete_twice(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda *a, **k: [])
    declaration = await continuity_engine.declare_travel(
        db_session, TENANT, uuid.uuid4(), MobilityClass.URBAN, "miami", str(uuid.uuid4())
    )
    await continuity_engine.complete_travel(db_session, TENANT, declaration.id)
    second = await continuity_engine.complete_travel(db_session, TENANT, declaration.id)
    assert second is None


async def test_list_active_declarations(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda *a, **k: [])
    member_id = uuid.uuid4()
    await continuity_engine.declare_travel(db_session, TENANT, member_id, MobilityClass.URBAN, "miami", str(uuid.uuid4()))
    active = await continuity_engine.list_active_declarations(db_session, TENANT, member_id)
    assert len(active) == 1
