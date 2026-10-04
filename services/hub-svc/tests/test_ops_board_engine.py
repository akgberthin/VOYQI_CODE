import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models import ChecklistStatus, TouchpointEventType, TouchpointSource, TouchpointStatus, CardStatus
from src.services import hub_engine, ops_board_engine
from src.services.ops_board_engine import ChecklistIncompleteError

TENANT = "voyqi-holdings"

MIAMI = (25.7959, -80.2870)
ORLANDO = (28.5383, -81.3792)  # ~350 km from Miami
LOS_ANGELES = (34.0522, -118.2437)  # ~3760 km from Miami


async def _make_hub(db_session, name, coords):
    from src.models import HubType

    return await hub_engine.create_hub(db_session, TENANT, name, HubType.CITY_BUSINESS, coords[0], coords[1])


# ── Touchpoints ───────────────────────────────────────────────────────

async def test_schedule_and_complete_touchpoint(db_session):
    hub = await _make_hub(db_session, "Miami Hub", MIAMI)
    event = await ops_board_engine.schedule_touchpoint(
        db_session, TENANT, hub.id, TouchpointEventType.PICKUP, TouchpointSource.SUBSCRIPTION, "member-1"
    )
    assert event.status == TouchpointStatus.SCHEDULED

    completed = await ops_board_engine.complete_touchpoint(db_session, TENANT, event.id)
    assert completed.status == TouchpointStatus.COMPLETED
    assert completed.completed_at is not None


async def test_flag_exception(db_session):
    hub = await _make_hub(db_session, "Miami Hub", MIAMI)
    event = await ops_board_engine.schedule_touchpoint(
        db_session, TENANT, hub.id, TouchpointEventType.RETURN, TouchpointSource.RENTAL, "renter-1"
    )
    flagged = await ops_board_engine.flag_exception(db_session, TENANT, event.id, "member no-show")
    assert flagged.status == TouchpointStatus.EXCEPTION
    assert flagged.exception_reason == "member no-show"


async def test_cannot_complete_twice(db_session):
    hub = await _make_hub(db_session, "Miami Hub", MIAMI)
    event = await ops_board_engine.schedule_touchpoint(db_session, TENANT, hub.id, TouchpointEventType.SWAP, TouchpointSource.SUBSCRIPTION, "member-2")
    await ops_board_engine.complete_touchpoint(db_session, TENANT, event.id)
    second = await ops_board_engine.complete_touchpoint(db_session, TENANT, event.id)
    assert second is None


# ── Prep checklists ───────────────────────────────────────────────────

async def test_cannot_mark_ready_with_open_items(db_session):
    hub = await _make_hub(db_session, "Miami Hub", MIAMI)
    checklist = await ops_board_engine.open_checklist(db_session, TENANT, hub.id, uuid.uuid4(), ["Wash exterior", "Check tire pressure"])
    with pytest.raises(ChecklistIncompleteError):
        await ops_board_engine.mark_checklist_ready(db_session, TENANT, checklist.id)


async def test_full_checklist_flow(db_session):
    hub = await _make_hub(db_session, "Miami Hub", MIAMI)
    checklist = await ops_board_engine.open_checklist(db_session, TENANT, hub.id, uuid.uuid4(), ["Wash exterior", "Check tire pressure"])
    items = await ops_board_engine.list_checklist_items(db_session, TENANT, checklist.id)
    assert len(items) == 2

    for item in items:
        await ops_board_engine.check_item(db_session, TENANT, item.id)

    ready = await ops_board_engine.mark_checklist_ready(db_session, TENANT, checklist.id)
    assert ready.status == ChecklistStatus.COMPLETED
    assert ready.completed_at is not None


async def test_mark_ready_unknown_checklist_raises(db_session):
    with pytest.raises(ValueError):
        await ops_board_engine.mark_checklist_ready(db_session, TENANT, uuid.uuid4())


# ── Card taps + geographic anomaly ────────────────────────────────────

async def test_first_tap_never_anomalous(db_session):
    hub = await _make_hub(db_session, "Miami Hub", MIAMI)
    card = await ops_board_engine.issue_card(db_session, TENANT, "member-1", "VQ-1001-UC-STD-2026")
    tap = await ops_board_engine.record_card_tap(db_session, TENANT, hub.id, card.id)
    assert tap.anomaly_flagged is False


async def test_plausible_travel_not_flagged(db_session):
    miami = await _make_hub(db_session, "Miami Hub", MIAMI)
    orlando = await _make_hub(db_session, "Orlando Hub", ORLANDO)
    card = await ops_board_engine.issue_card(db_session, TENANT, "member-2", "VQ-1002-UC-STD-2026")

    first = await ops_board_engine.record_card_tap(db_session, TENANT, miami.id, card.id)
    # backdate the first tap by 4 hours — ~350km / 4h = ~87 km/h, plausible
    first_row = await db_session.get(type(first), first.id)
    first_row.tapped_at = datetime.now(timezone.utc) - timedelta(hours=4)
    await db_session.commit()

    second = await ops_board_engine.record_card_tap(db_session, TENANT, orlando.id, card.id)
    assert second.anomaly_flagged is False

    await db_session.refresh(card)
    assert card.status == CardStatus.ACTIVE


async def test_impossible_travel_flagged_and_card_suspended(db_session):
    miami = await _make_hub(db_session, "Miami Hub", MIAMI)
    la = await _make_hub(db_session, "LA Hub", LOS_ANGELES)
    card = await ops_board_engine.issue_card(db_session, TENANT, "member-3", "VQ-1003-UC-STD-2026")

    first = await ops_board_engine.record_card_tap(db_session, TENANT, miami.id, card.id)
    # backdate by 5 minutes — ~3760km / (5/60)h implies ~45,000 km/h
    first_row = await db_session.get(type(first), first.id)
    first_row.tapped_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db_session.commit()

    second = await ops_board_engine.record_card_tap(db_session, TENANT, la.id, card.id)
    assert second.anomaly_flagged is True
    assert "km/h" in second.anomaly_reason

    await db_session.refresh(card)
    assert card.status == CardStatus.SUSPENDED


async def test_reactivate_card(db_session):
    hub = await _make_hub(db_session, "Miami Hub", MIAMI)
    la = await _make_hub(db_session, "LA Hub", LOS_ANGELES)
    card = await ops_board_engine.issue_card(db_session, TENANT, "member-4", "VQ-1004-UC-STD-2026")
    first = await ops_board_engine.record_card_tap(db_session, TENANT, hub.id, card.id)
    first_row = await db_session.get(type(first), first.id)
    first_row.tapped_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db_session.commit()
    await ops_board_engine.record_card_tap(db_session, TENANT, la.id, card.id)

    reactivated = await ops_board_engine.reactivate_card(db_session, TENANT, card.id)
    assert reactivated.status == CardStatus.ACTIVE


async def test_cannot_reactivate_active_card(db_session):
    card = await ops_board_engine.issue_card(db_session, TENANT, "member-5", "VQ-1005-UC-STD-2026")
    result = await ops_board_engine.reactivate_card(db_session, TENANT, card.id)
    assert result is None  # not suspended, nothing to reactivate


async def test_tap_unknown_hub_raises(db_session):
    card = await ops_board_engine.issue_card(db_session, TENANT, "member-6", "VQ-1006-UC-STD-2026")
    with pytest.raises(ValueError):
        await ops_board_engine.record_card_tap(db_session, TENANT, uuid.uuid4(), card.id)


async def test_tap_unknown_card_raises(db_session):
    hub = await _make_hub(db_session, "Miami Hub", MIAMI)
    with pytest.raises(ValueError):
        await ops_board_engine.record_card_tap(db_session, TENANT, hub.id, uuid.uuid4())


async def test_same_hub_repeat_tap_never_anomalous(db_session):
    """Tapping the same hub twice in quick succession (e.g. entry then
    exit) is normal and must never trigger the distance/speed check."""
    hub = await _make_hub(db_session, "Miami Hub", MIAMI)
    card = await ops_board_engine.issue_card(db_session, TENANT, "member-7", "VQ-1007-UC-STD-2026")
    await ops_board_engine.record_card_tap(db_session, TENANT, hub.id, card.id)
    second = await ops_board_engine.record_card_tap(db_session, TENANT, hub.id, card.id)
    assert second.anomaly_flagged is False


async def test_list_taps_for_card_ordered_most_recent_first(db_session):
    hub1 = await _make_hub(db_session, "Miami Hub", MIAMI)
    hub2 = await _make_hub(db_session, "Orlando Hub", ORLANDO)
    card = await ops_board_engine.issue_card(db_session, TENANT, "member-8", "VQ-1008-UC-STD-2026")
    first = await ops_board_engine.record_card_tap(db_session, TENANT, hub1.id, card.id)
    first_row = await db_session.get(type(first), first.id)
    first_row.tapped_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.commit()
    await ops_board_engine.record_card_tap(db_session, TENANT, hub2.id, card.id)

    taps = await ops_board_engine.list_taps_for_card(db_session, TENANT, card.id)
    assert len(taps) == 2
    assert taps[0].hub_id == hub2.id  # most recent first
