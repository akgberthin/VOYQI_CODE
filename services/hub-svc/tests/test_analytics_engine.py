import uuid
from datetime import datetime, timedelta, timezone

from src.models import HubType, TouchpointEventType, TouchpointSource
from src.services import analytics_engine, hub_engine, ops_board_engine

TENANT = "voyqi-holdings"


async def _make_hub(db_session, capacity=10):
    return await hub_engine.create_hub(db_session, TENANT, "Miami Hub", HubType.CITY_BUSINESS, 25.79, -80.28, capacity=capacity)


async def test_throughput_counts_completed_within_window(db_session):
    hub = await _make_hub(db_session)
    e1 = await ops_board_engine.schedule_touchpoint(db_session, TENANT, hub.id, TouchpointEventType.PICKUP, TouchpointSource.SUBSCRIPTION, "m1")
    await ops_board_engine.complete_touchpoint(db_session, TENANT, e1.id)
    await ops_board_engine.schedule_touchpoint(db_session, TENANT, hub.id, TouchpointEventType.RETURN, TouchpointSource.RENTAL, "m2")
    # leave e2 scheduled — shouldn't count

    result = await analytics_engine.get_throughput(db_session, TENANT, hub.id, window_days=7)
    assert result["completed_touchpoints"] == 1
    assert result["touchpoints_per_day"] == round(1 / 7, 2)


async def test_throughput_excludes_old_events_outside_window(db_session):
    hub = await _make_hub(db_session)
    e1 = await ops_board_engine.schedule_touchpoint(db_session, TENANT, hub.id, TouchpointEventType.PICKUP, TouchpointSource.SUBSCRIPTION, "m1")
    completed = await ops_board_engine.complete_touchpoint(db_session, TENANT, e1.id)
    row = await db_session.get(type(completed), completed.id)
    row.completed_at = datetime.now(timezone.utc) - timedelta(days=30)
    await db_session.commit()

    result = await analytics_engine.get_throughput(db_session, TENANT, hub.id, window_days=7)
    assert result["completed_touchpoints"] == 0


async def test_dwell_time_computed_from_scheduled_to_completed(db_session):
    hub = await _make_hub(db_session)
    e1 = await ops_board_engine.schedule_touchpoint(db_session, TENANT, hub.id, TouchpointEventType.SWAP, TouchpointSource.SUBSCRIPTION, "m1")
    row = await db_session.get(type(e1), e1.id)
    row.scheduled_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.commit()
    await ops_board_engine.complete_touchpoint(db_session, TENANT, e1.id)

    result = await analytics_engine.get_dwell_time_hours(db_session, TENANT, hub.id)
    assert result["sample_size"] == 1
    assert 1.9 <= result["avg_dwell_hours"] <= 2.1


async def test_dwell_time_no_data_returns_none(db_session):
    hub = await _make_hub(db_session)
    result = await analytics_engine.get_dwell_time_hours(db_session, TENANT, hub.id)
    assert result["avg_dwell_hours"] is None
    assert result["sample_size"] == 0


async def test_capacity_utilization_counts_scheduled_only(db_session):
    hub = await _make_hub(db_session, capacity=4)
    for i in range(3):
        await ops_board_engine.schedule_touchpoint(db_session, TENANT, hub.id, TouchpointEventType.PICKUP, TouchpointSource.SUBSCRIPTION, f"m{i}")

    result = await analytics_engine.get_capacity_utilization(db_session, TENANT, hub.id)
    assert result["vehicles_scheduled"] == 3
    assert result["capacity"] == 4
    assert result["utilization_pct"] == 75.0
    assert result["at_capacity"] is False


async def test_capacity_at_full_utilization(db_session):
    hub = await _make_hub(db_session, capacity=2)
    for i in range(2):
        await ops_board_engine.schedule_touchpoint(db_session, TENANT, hub.id, TouchpointEventType.PICKUP, TouchpointSource.SUBSCRIPTION, f"m{i}")

    result = await analytics_engine.get_capacity_utilization(db_session, TENANT, hub.id)
    assert result["utilization_pct"] == 100.0
    assert result["at_capacity"] is True


async def test_capacity_unknown_hub_returns_error(db_session):
    result = await analytics_engine.get_capacity_utilization(db_session, TENANT, uuid.uuid4())
    assert "error" in result


async def test_network_snapshot_covers_all_active_hubs(db_session):
    await hub_engine.create_hub(db_session, TENANT, "Hub A", HubType.AIRPORT, 25.79, -80.28, capacity=10)
    hub2 = await hub_engine.create_hub(db_session, TENANT, "Hub B", HubType.RESIDENTIAL, 28.53, -81.37, capacity=5)
    await hub_engine.deactivate_hub(db_session, TENANT, hub2.id)

    snapshot = await analytics_engine.get_network_snapshot(db_session, TENANT)
    assert len(snapshot) == 1
    assert snapshot[0]["name"] == "Hub A"
