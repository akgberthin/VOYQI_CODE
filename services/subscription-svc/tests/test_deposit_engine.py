import uuid

import pytest

from src.models import DepositHoldStatus, MobilityClass
from src.services import deposit_engine
from src.services.deposit_engine import (
    DepositAmountUndefinedError,
    DepositNotPendingCaptureError,
    DepositNotPlacedError,
)

TENANT = "voyqi-holdings"


async def test_place_deposit_hold_luxury_plus_uses_fixed_table_amount(db_session):
    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.LUXURY_PLUS)
    assert hold.status == DepositHoldStatus.PLACED
    assert hold.amount_usd == 10000.0


async def test_place_deposit_hold_performance_plus_uses_fixed_table_amount(db_session):
    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.PERFORMANCE_PLUS)
    assert hold.amount_usd == 15000.0


async def test_place_deposit_hold_rejects_class_with_no_deposit_requirement(db_session):
    with pytest.raises(DepositAmountUndefinedError):
        await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.URBAN)


async def test_release_deposit_hold_full_return_is_autonomous(db_session):
    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.LUXURY_PLUS)
    released = await deposit_engine.release_deposit_hold(db_session, TENANT, hold.id)
    assert released.status == DepositHoldStatus.RELEASED
    assert released.released_at is not None
    assert released.resolved_at is not None


async def test_cannot_release_already_released_hold(db_session):
    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.LUXURY_PLUS)
    await deposit_engine.release_deposit_hold(db_session, TENANT, hold.id)
    with pytest.raises(DepositNotPlacedError):
        await deposit_engine.release_deposit_hold(db_session, TENANT, hold.id)


async def test_capture_request_always_lands_in_pending_review_not_executed_directly(db_session, monkeypatch):
    from src.services import ai_orchestration_tools
    monkeypatch.setattr(ai_orchestration_tools, "request_deposit_capture_review", lambda *a, **k: None)

    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.PERFORMANCE_PLUS)
    pending, _evaluation = await deposit_engine.request_capture(db_session, TENANT, hold.id, 2500.0, "Windshield damage during rental")
    assert pending.status == DepositHoldStatus.CAPTURE_PENDING_REVIEW
    assert pending.captured_amount_usd == 2500.0


async def test_capture_amount_cannot_exceed_held_amount(db_session):
    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.LUXURY_PLUS)
    with pytest.raises(ValueError):
        await deposit_engine.request_capture(db_session, TENANT, hold.id, 999999.0, "Implausible amount")


async def test_approve_capture_resolves_the_hold(db_session):
    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.LUXURY_PLUS)
    await deposit_engine.request_capture(db_session, TENANT, hold.id, 1000.0, "Cleaning fee")
    approved = await deposit_engine.approve_capture(db_session, TENANT, hold.id)
    assert approved.status == DepositHoldStatus.CAPTURED
    assert approved.resolved_at is not None


async def test_reject_capture_returns_hold_to_placed_with_no_money_taken(db_session):
    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.LUXURY_PLUS)
    await deposit_engine.request_capture(db_session, TENANT, hold.id, 1000.0, "Disputed charge")
    rejected = await deposit_engine.reject_capture(db_session, TENANT, hold.id)
    assert rejected.status == DepositHoldStatus.PLACED
    assert rejected.captured_amount_usd == 0.0


async def test_cannot_approve_capture_that_was_never_requested(db_session):
    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.LUXURY_PLUS)
    with pytest.raises(DepositNotPendingCaptureError):
        await deposit_engine.approve_capture(db_session, TENANT, hold.id)


async def test_list_deposit_holds_filters_by_member(db_session):
    member_a = uuid.uuid4()
    member_b = uuid.uuid4()
    await deposit_engine.place_deposit_hold(db_session, TENANT, member_a, MobilityClass.LUXURY_PLUS)
    await deposit_engine.place_deposit_hold(db_session, TENANT, member_b, MobilityClass.PERFORMANCE_PLUS)

    holds = await deposit_engine.list_deposit_holds(db_session, TENANT, member_profile_id=member_a)
    assert len(holds) == 1
    assert holds[0].member_profile_id == member_a


async def test_request_capture_surfaces_compliance_evaluation_when_available(db_session, monkeypatch):
    from src.services import ai_orchestration_tools
    monkeypatch.setattr(
        ai_orchestration_tools, "request_deposit_capture_review",
        lambda tenant_id, hold_id, amount_usd, reason: {"risk_score": 55.0, "action_type": "review_deposit_capture"},
    )

    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.LUXURY_PLUS)
    _pending, evaluation = await deposit_engine.request_capture(db_session, TENANT, hold.id, 3000.0, "Damage assessment")
    assert evaluation == {"risk_score": 55.0, "action_type": "review_deposit_capture"}


async def test_request_capture_still_lands_pending_review_even_when_compliance_agent_unreachable(db_session, monkeypatch):
    """The mandatory human gate never depends on this call succeeding
    — deposit_engine's own state machine is what actually guarantees
    it, not the AI evaluation."""
    from src.services import ai_orchestration_tools
    monkeypatch.setattr(ai_orchestration_tools, "request_deposit_capture_review", lambda *a, **k: None)

    hold = await deposit_engine.place_deposit_hold(db_session, TENANT, uuid.uuid4(), MobilityClass.LUXURY_PLUS)
    pending, evaluation = await deposit_engine.request_capture(db_session, TENANT, hold.id, 3000.0, "Damage assessment")
    assert pending.status == DepositHoldStatus.CAPTURE_PENDING_REVIEW
    assert evaluation is None
