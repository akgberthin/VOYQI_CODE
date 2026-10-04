import uuid

from src.models import DrivingEventType
from src.services import safety_scoring_engine

TENANT = "voyqi-holdings"


async def test_log_and_list_driving_events(db_session):
    vehicle_id = uuid.uuid4()
    await safety_scoring_engine.log_driving_event(db_session, TENANT, vehicle_id, DrivingEventType.HARSH_BRAKING)
    await safety_scoring_engine.log_driving_event(db_session, TENANT, vehicle_id, DrivingEventType.SPEEDING)

    events = await safety_scoring_engine.list_driving_events(db_session, TENANT, vehicle_id)
    assert len(events) == 2


async def test_record_and_get_latest_safety_score(db_session):
    vehicle_id = uuid.uuid4()
    await safety_scoring_engine.record_safety_score(db_session, TENANT, vehicle_id, 85.0, "few minor events")
    latest = await safety_scoring_engine.record_safety_score(db_session, TENANT, vehicle_id, 72.0, "more events since last assessment")

    result = await safety_scoring_engine.get_latest_safety_score(db_session, TENANT, vehicle_id)
    assert result.id == latest.id
    assert result.score == 72.0


async def test_get_latest_safety_score_no_history_returns_none(db_session):
    result = await safety_scoring_engine.get_latest_safety_score(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_list_vehicles_below_score_uses_most_recent_only(db_session):
    vehicle_a = uuid.uuid4()
    vehicle_b = uuid.uuid4()
    await safety_scoring_engine.record_safety_score(db_session, TENANT, vehicle_a, 40.0, "old low score")
    await safety_scoring_engine.record_safety_score(db_session, TENANT, vehicle_a, 90.0, "improved since")  # most recent — should NOT show as below threshold
    await safety_scoring_engine.record_safety_score(db_session, TENANT, vehicle_b, 55.0, "consistently low")

    watchlist = await safety_scoring_engine.list_vehicles_below_score(db_session, TENANT, 60.0)
    watchlist_vehicle_ids = {s.vehicle_id for s in watchlist}
    assert vehicle_a not in watchlist_vehicle_ids
    assert vehicle_b in watchlist_vehicle_ids
