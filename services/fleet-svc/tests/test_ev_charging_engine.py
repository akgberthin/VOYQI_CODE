import uuid

from src.services import ev_charging_engine

TENANT = "voyqi-holdings"


async def test_charging_session_lifecycle(db_session):
    vehicle_id = uuid.uuid4()
    session = await ev_charging_engine.start_charging_session(db_session, TENANT, vehicle_id, station_location="Miami Beach Hub — Bay 3")
    assert session.status.value == "in_progress"

    completed = await ev_charging_engine.complete_charging_session(db_session, TENANT, session.id, energy_added_kwh=42.5, cost_usd=12.75)
    assert completed.status.value == "completed"
    assert completed.energy_added_kwh == 42.5
    assert completed.ended_at is not None


async def test_cannot_complete_session_twice(db_session):
    vehicle_id = uuid.uuid4()
    session = await ev_charging_engine.start_charging_session(db_session, TENANT, vehicle_id)
    await ev_charging_engine.complete_charging_session(db_session, TENANT, session.id, 30.0, 9.0)

    result = await ev_charging_engine.complete_charging_session(db_session, TENANT, session.id, 30.0, 9.0)
    assert result is None


async def test_charging_cost_summary_exact_arithmetic(db_session):
    vehicle_id = uuid.uuid4()
    s1 = await ev_charging_engine.start_charging_session(db_session, TENANT, vehicle_id)
    await ev_charging_engine.complete_charging_session(db_session, TENANT, s1.id, 40.0, 12.0)
    s2 = await ev_charging_engine.start_charging_session(db_session, TENANT, vehicle_id)
    await ev_charging_engine.complete_charging_session(db_session, TENANT, s2.id, 38.0, 11.4)

    summary = await ev_charging_engine.get_charging_cost_summary(db_session, TENANT, vehicle_id)
    assert summary["session_count"] == 2
    assert summary["total_kwh"] == 78.0
    assert summary["total_cost_usd"] == 23.4
    assert summary["avg_cost_per_kwh_usd"] == round(23.4 / 78.0, 4)


async def test_charging_cost_summary_excludes_in_progress_sessions(db_session):
    vehicle_id = uuid.uuid4()
    await ev_charging_engine.start_charging_session(db_session, TENANT, vehicle_id)  # never completed

    summary = await ev_charging_engine.get_charging_cost_summary(db_session, TENANT, vehicle_id)
    assert summary["session_count"] == 0
    assert summary["avg_cost_per_kwh_usd"] is None


async def test_battery_health_recording_and_latest(db_session):
    vehicle_id = uuid.uuid4()
    await ev_charging_engine.record_battery_health(db_session, TENANT, vehicle_id, state_of_charge_pct=65.0, state_of_health_pct=94.0)
    latest = await ev_charging_engine.record_battery_health(db_session, TENANT, vehicle_id, state_of_charge_pct=40.0, state_of_health_pct=92.5)

    result = await ev_charging_engine.get_latest_battery_health(db_session, TENANT, vehicle_id)
    assert result.id == latest.id
    assert result.state_of_health_pct == 92.5


def test_degradation_alert_threshold_exact():
    assert ev_charging_engine.is_degradation_alert(79.9) is True
    assert ev_charging_engine.is_degradation_alert(80.0) is False
    assert ev_charging_engine.is_degradation_alert(95.0) is False


async def test_battery_degradation_watchlist_uses_most_recent_only(db_session):
    vehicle_a = uuid.uuid4()
    vehicle_b = uuid.uuid4()
    await ev_charging_engine.record_battery_health(db_session, TENANT, vehicle_a, 60.0, 65.0)  # old low reading
    await ev_charging_engine.record_battery_health(db_session, TENANT, vehicle_a, 60.0, 88.0)  # most recent — replaced battery, should NOT be on watchlist
    await ev_charging_engine.record_battery_health(db_session, TENANT, vehicle_b, 55.0, 72.0)  # consistently degraded

    watchlist = await ev_charging_engine.list_battery_degradation_watchlist(db_session, TENANT)
    watchlist_ids = {r.vehicle_id for r in watchlist}
    assert vehicle_a not in watchlist_ids
    assert vehicle_b in watchlist_ids


async def test_list_battery_health_readings_in_order(db_session):
    vehicle_id = uuid.uuid4()
    await ev_charging_engine.record_battery_health(db_session, TENANT, vehicle_id, 80.0, 95.0)
    await ev_charging_engine.record_battery_health(db_session, TENANT, vehicle_id, 80.0, 92.0)

    readings = await ev_charging_engine.list_battery_health_readings(db_session, TENANT, vehicle_id)
    assert len(readings) == 2
    assert readings[0].state_of_health_pct == 95.0
    assert readings[1].state_of_health_pct == 92.0


async def test_record_and_get_latest_battery_degradation_forecast(db_session):
    vehicle_id = uuid.uuid4()
    await ev_charging_engine.record_battery_degradation_forecast(db_session, TENANT, vehicle_id, -0.05, 240, "steady decline observed")

    latest = await ev_charging_engine.get_latest_battery_degradation_forecast(db_session, TENANT, vehicle_id)
    assert latest.estimated_days_to_threshold == 240
    assert latest.degradation_rate_pct_per_day == -0.05


async def test_get_latest_forecast_none_when_never_computed(db_session):
    result = await ev_charging_engine.get_latest_battery_degradation_forecast(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_forecast_with_no_estimated_days_when_battery_not_declining(db_session):
    """A stable or improving battery has no meaningful
    days-to-threshold — this must be representable as None, not a
    fabricated number."""
    vehicle_id = uuid.uuid4()
    await ev_charging_engine.record_battery_degradation_forecast(db_session, TENANT, vehicle_id, 0.0, None, "no measurable decline")

    latest = await ev_charging_engine.get_latest_battery_degradation_forecast(db_session, TENANT, vehicle_id)
    assert latest.estimated_days_to_threshold is None
