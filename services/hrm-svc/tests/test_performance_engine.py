import uuid
from datetime import datetime, timezone

import pytest

from src.models import BoxLevel, GoalStatus
from src.services import employee_engine, performance_engine
from src.services.performance_engine import (
    FeedbackDraftNotPendingError,
    NineBoxNotPendingError,
)

TENANT = "voyqi-holdings"


async def test_goal_completion_rate(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    g1 = await performance_engine.create_goal(db_session, TENANT, emp.id, "Increase CSI score")
    await performance_engine.create_goal(db_session, TENANT, emp.id, "Complete DMS certification")
    await performance_engine.update_goal_status(db_session, TENANT, g1.id, GoalStatus.COMPLETED)

    rate = await performance_engine.get_goal_completion_rate(db_session, TENANT, emp.id)
    assert rate == 0.5


async def test_goal_completion_rate_no_goals_is_zero(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    rate = await performance_engine.get_goal_completion_rate(db_session, TENANT, emp.id)
    assert rate == 0.0


async def test_review_cycle_lifecycle(db_session):
    cycle = await performance_engine.create_review_cycle(db_session, TENANT, "2026 H1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    assert cycle.status.value == "open"
    closed = await performance_engine.close_review_cycle(db_session, TENANT, cycle.id)
    assert closed.status.value == "closed"


async def test_create_review_and_finalize_feedback(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    cycle = await performance_engine.create_review_cycle(db_session, TENANT, "2026 H1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    review = await performance_engine.create_review(db_session, TENANT, emp.id, cycle.id, rating=4.2, manager_notes="Strong quarter, exceeded CSI targets")
    assert review.status.value == "draft"

    finalized = await performance_engine.finalize_feedback(db_session, TENANT, review.id, "Jane had an excellent quarter, exceeding CSI targets consistently.")
    assert finalized.status.value == "finalized"
    assert "excellent quarter" in finalized.finalized_feedback


async def test_feedback_draft_hard_flow(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    cycle = await performance_engine.create_review_cycle(db_session, TENANT, "2026 H1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    review = await performance_engine.create_review(db_session, TENANT, emp.id, cycle.id, rating=4.2, manager_notes="Strong quarter")
    draft = await performance_engine.create_feedback_draft(db_session, TENANT, review.id, "Jane demonstrated excellent performance this quarter.", reasoning="polished from manager notes")

    pending = await performance_engine.list_pending_feedback_drafts(db_session, TENANT, review.id)
    assert len(pending) == 1

    approved_draft, finalized_review = await performance_engine.approve_feedback_draft(db_session, TENANT, draft.id)
    assert approved_draft.status.value == "approved_finalized"
    assert finalized_review.status.value == "finalized"
    assert finalized_review.finalized_feedback == "Jane demonstrated excellent performance this quarter."


async def test_feedback_draft_can_be_edited_before_finalizing(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    cycle = await performance_engine.create_review_cycle(db_session, TENANT, "2026 H1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    review = await performance_engine.create_review(db_session, TENANT, emp.id, cycle.id)
    draft = await performance_engine.create_feedback_draft(db_session, TENANT, review.id, "Generic draft")

    _, finalized_review = await performance_engine.approve_feedback_draft(db_session, TENANT, draft.id, final_text="Manager-edited final version")
    assert finalized_review.finalized_feedback == "Manager-edited final version"


async def test_reject_feedback_draft(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    cycle = await performance_engine.create_review_cycle(db_session, TENANT, "2026 H1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    review = await performance_engine.create_review(db_session, TENANT, emp.id, cycle.id)
    draft = await performance_engine.create_feedback_draft(db_session, TENANT, review.id, "x")

    rejected = await performance_engine.reject_feedback_draft(db_session, TENANT, draft.id)
    assert rejected.status.value == "rejected"

    unchanged_review = await performance_engine.get_review(db_session, TENANT, review.id)
    assert unchanged_review.status.value == "draft"


async def test_cannot_approve_feedback_draft_twice(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    cycle = await performance_engine.create_review_cycle(db_session, TENANT, "2026 H1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    review = await performance_engine.create_review(db_session, TENANT, emp.id, cycle.id)
    draft = await performance_engine.create_feedback_draft(db_session, TENANT, review.id, "x")
    await performance_engine.approve_feedback_draft(db_session, TENANT, draft.id)

    with pytest.raises(FeedbackDraftNotPendingError):
        await performance_engine.approve_feedback_draft(db_session, TENANT, draft.id)


async def test_nine_box_placement_confirm_flow(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    cycle = await performance_engine.create_review_cycle(db_session, TENANT, "2026 H1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    placement = await performance_engine.create_nine_box_placement(db_session, TENANT, emp.id, cycle.id, BoxLevel.HIGH, BoxLevel.HIGH, reasoning="top performer, high goal completion")
    assert placement.status.value == "ai_suggested"

    confirmed = await performance_engine.confirm_nine_box_placement(db_session, TENANT, placement.id)
    assert confirmed.status.value == "confirmed"
    assert confirmed.confirmed_at is not None


async def test_cannot_confirm_nine_box_twice(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    cycle = await performance_engine.create_review_cycle(db_session, TENANT, "2026 H1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    placement = await performance_engine.create_nine_box_placement(db_session, TENANT, emp.id, cycle.id, BoxLevel.HIGH, BoxLevel.HIGH)
    await performance_engine.confirm_nine_box_placement(db_session, TENANT, placement.id)

    with pytest.raises(NineBoxNotPendingError):
        await performance_engine.confirm_nine_box_placement(db_session, TENANT, placement.id)


async def test_list_nine_box_grid_for_cycle(db_session):
    emp1 = await employee_engine.create_employee(db_session, TENANT, "A")
    emp2 = await employee_engine.create_employee(db_session, TENANT, "B")
    cycle = await performance_engine.create_review_cycle(db_session, TENANT, "2026 H1", datetime.now(timezone.utc), datetime.now(timezone.utc))
    await performance_engine.create_nine_box_placement(db_session, TENANT, emp1.id, cycle.id, BoxLevel.HIGH, BoxLevel.HIGH)
    await performance_engine.create_nine_box_placement(db_session, TENANT, emp2.id, cycle.id, BoxLevel.LOW, BoxLevel.MEDIUM)

    grid = await performance_engine.list_nine_box_grid(db_session, TENANT, cycle.id)
    assert len(grid) == 2


async def test_confirm_unknown_nine_box_raises(db_session):
    with pytest.raises(NineBoxNotPendingError):
        await performance_engine.confirm_nine_box_placement(db_session, TENANT, uuid.uuid4())
