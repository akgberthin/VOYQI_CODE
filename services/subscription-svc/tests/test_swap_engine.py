import uuid
from datetime import datetime, timedelta, timezone

from src.models import MobilityClass, SwapRequestStatus
from src.services import fleet_tools, swap_engine

TENANT = "voyqi-holdings"


async def test_request_swap_sets_sla_deadline(db_session):
    request = await swap_engine.request_swap(db_session, TENANT, uuid.uuid4(), MobilityClass.EXECUTIVE)
    assert request.status == SwapRequestStatus.REQUESTED
    sla_due_at = request.sla_due_at
    if sla_due_at.tzinfo is None:
        sla_due_at = sla_due_at.replace(tzinfo=timezone.utc)
    assert sla_due_at > datetime.now(timezone.utc)


async def test_stage_fulfillment_with_available_vehicle(db_session, monkeypatch):
    fake_vehicle_id = str(uuid.uuid4())
    fake_transfer_id = str(uuid.uuid4())
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda tenant_id, vehicle_class, hub_id=None: [{"id": fake_vehicle_id}])
    monkeypatch.setattr(fleet_tools, "propose_transfer", lambda tenant_id, vehicle_id, to_hub_id, reason: {"id": fake_transfer_id, "status": "proposed"})

    request = await swap_engine.request_swap(db_session, TENANT, uuid.uuid4(), MobilityClass.EXECUTIVE)
    staged = await swap_engine.stage_fulfillment(db_session, TENANT, request.id, str(uuid.uuid4()))
    assert staged.status == SwapRequestStatus.STAGED
    assert str(staged.fleet_transfer_order_id) == fake_transfer_id


async def test_stage_fulfillment_no_availability_stays_requested(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda *a, **k: [])
    request = await swap_engine.request_swap(db_session, TENANT, uuid.uuid4(), MobilityClass.LUXURY_PLUS)
    result = await swap_engine.stage_fulfillment(db_session, TENANT, request.id, str(uuid.uuid4()))
    assert result.status == SwapRequestStatus.REQUESTED


async def test_fulfill_swap(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda *a, **k: [{"id": str(uuid.uuid4())}])
    monkeypatch.setattr(fleet_tools, "propose_transfer", lambda *a, **k: {"id": str(uuid.uuid4())})

    request = await swap_engine.request_swap(db_session, TENANT, uuid.uuid4(), MobilityClass.URBAN)
    await swap_engine.stage_fulfillment(db_session, TENANT, request.id, str(uuid.uuid4()))
    fulfilled = await swap_engine.fulfill_swap(db_session, TENANT, request.id)
    assert fulfilled.status == SwapRequestStatus.FULFILLED
    assert fulfilled.fulfilled_at is not None


async def test_cannot_fulfill_unstaged_request(db_session):
    request = await swap_engine.request_swap(db_session, TENANT, uuid.uuid4(), MobilityClass.URBAN)
    result = await swap_engine.fulfill_swap(db_session, TENANT, request.id)
    assert result is None


async def test_sla_breach_detection(db_session):
    request = await swap_engine.request_swap(db_session, TENANT, uuid.uuid4(), MobilityClass.URBAN)
    # simulate time passing
    request.sla_due_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.commit()

    checked = await swap_engine.check_sla_breach(db_session, TENANT, request.id)
    assert checked.status == SwapRequestStatus.SLA_BREACHED


async def test_no_sla_breach_when_within_window(db_session):
    request = await swap_engine.request_swap(db_session, TENANT, uuid.uuid4(), MobilityClass.URBAN)
    checked = await swap_engine.check_sla_breach(db_session, TENANT, request.id)
    assert checked.status == SwapRequestStatus.REQUESTED


async def test_list_swap_requests_filtered_by_status(db_session):
    await swap_engine.request_swap(db_session, TENANT, uuid.uuid4(), MobilityClass.URBAN)
    requested = await swap_engine.list_swap_requests(db_session, TENANT, status=SwapRequestStatus.REQUESTED)
    assert len(requested) == 1
