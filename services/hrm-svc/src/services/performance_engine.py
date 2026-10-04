"""
HRM-004 Performance & Talent Development.

request_feedback_draft publishes hrm.feedback_draft_requested — the
Talent Agent turns a manager's raw notes into polished narrative
feedback automatically, but finalize_feedback (the only function that
writes onto PerformanceReview.finalized_feedback) is always a manager's
action, editable before it's saved. request_nine_box_evaluation
publishes hrm.nine_box_evaluation_requested the instant a review cycle
closes for an employee; confirm_nine_box_placement is the sole human-
confirmed action.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    FeedbackDraft,
    FeedbackDraftStatus,
    Goal,
    GoalStatus,
    NineBoxPlacement,
    NineBoxStatus,
    PerformanceReview,
    ReviewCycle,
    ReviewCycleStatus,
    ReviewStatus,
)


class FeedbackDraftNotPendingError(ValueError):
    pass


class NineBoxNotPendingError(ValueError):
    pass


async def create_goal(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, title: str, description: str = "", target_date: datetime | None = None) -> Goal:
    goal = Goal(tenant_id=tenant_id, employee_id=employee_id, title=title, description=description, target_date=target_date)
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return goal


async def list_goals(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[Goal]:
    result = await db.execute(select(Goal).where(Goal.tenant_id == tenant_id, Goal.employee_id == employee_id))
    return list(result.scalars().all())


async def update_goal_status(db: AsyncSession, tenant_id: str, goal_id: uuid.UUID, status: GoalStatus) -> Goal | None:
    result = await db.execute(select(Goal).where(Goal.id == goal_id, Goal.tenant_id == tenant_id))
    goal = result.scalar_one_or_none()
    if goal is None:
        return None
    goal.status = status
    await db.commit()
    await db.refresh(goal)
    return goal


async def get_goal_completion_rate(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> float:
    goals = await list_goals(db, tenant_id, employee_id)
    if not goals:
        return 0.0
    completed = len([g for g in goals if g.status == GoalStatus.COMPLETED])
    return round(completed / len(goals), 2)


async def create_review_cycle(db: AsyncSession, tenant_id: str, name: str, start_date: datetime, end_date: datetime) -> ReviewCycle:
    cycle = ReviewCycle(tenant_id=tenant_id, name=name, start_date=start_date, end_date=end_date)
    db.add(cycle)
    await db.commit()
    await db.refresh(cycle)
    return cycle


async def close_review_cycle(db: AsyncSession, tenant_id: str, cycle_id: uuid.UUID) -> ReviewCycle | None:
    result = await db.execute(select(ReviewCycle).where(ReviewCycle.id == cycle_id, ReviewCycle.tenant_id == tenant_id))
    cycle = result.scalar_one_or_none()
    if cycle is None:
        return None
    cycle.status = ReviewCycleStatus.CLOSED
    await db.commit()
    await db.refresh(cycle)
    return cycle


async def get_review_cycle(db: AsyncSession, tenant_id: str, cycle_id: uuid.UUID) -> ReviewCycle | None:
    result = await db.execute(select(ReviewCycle).where(ReviewCycle.id == cycle_id, ReviewCycle.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def create_review(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, review_cycle_id: uuid.UUID, rating: float | None = None, manager_notes: str = "", reviewer_id: uuid.UUID | None = None) -> PerformanceReview:
    review = PerformanceReview(tenant_id=tenant_id, employee_id=employee_id, review_cycle_id=review_cycle_id, rating=rating, manager_notes=manager_notes, reviewer_id=reviewer_id)
    db.add(review)
    await db.commit()
    await db.refresh(review)
    return review


async def get_review(db: AsyncSession, tenant_id: str, review_id: uuid.UUID) -> PerformanceReview | None:
    result = await db.execute(select(PerformanceReview).where(PerformanceReview.id == review_id, PerformanceReview.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_reviews_for_cycle(db: AsyncSession, tenant_id: str, review_cycle_id: uuid.UUID) -> list[PerformanceReview]:
    result = await db.execute(select(PerformanceReview).where(PerformanceReview.tenant_id == tenant_id, PerformanceReview.review_cycle_id == review_cycle_id))
    return list(result.scalars().all())


async def finalize_feedback(db: AsyncSession, tenant_id: str, review_id: uuid.UUID, feedback_text: str) -> PerformanceReview | None:
    review = await get_review(db, tenant_id, review_id)
    if review is None:
        return None
    review.finalized_feedback = feedback_text
    review.status = ReviewStatus.FINALIZED
    await db.commit()
    await db.refresh(review)
    return review


async def create_feedback_draft(db: AsyncSession, tenant_id: str, review_id: uuid.UUID, draft_body: str, reasoning: str = "") -> FeedbackDraft:
    draft = FeedbackDraft(tenant_id=tenant_id, review_id=review_id, draft_body=draft_body, reasoning=reasoning)
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def list_pending_feedback_drafts(db: AsyncSession, tenant_id: str, review_id: uuid.UUID | None = None) -> list[FeedbackDraft]:
    stmt = select(FeedbackDraft).where(FeedbackDraft.tenant_id == tenant_id, FeedbackDraft.status == FeedbackDraftStatus.PENDING_REVIEW)
    if review_id:
        stmt = stmt.where(FeedbackDraft.review_id == review_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def approve_feedback_draft(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID, final_text: str | None = None) -> tuple[FeedbackDraft, PerformanceReview]:
    result = await db.execute(select(FeedbackDraft).where(FeedbackDraft.id == draft_id, FeedbackDraft.tenant_id == tenant_id))
    draft = result.scalar_one_or_none()
    if draft is None or draft.status != FeedbackDraftStatus.PENDING_REVIEW:
        raise FeedbackDraftNotPendingError("draft not found or already reviewed")

    text_to_finalize = final_text if final_text is not None else draft.draft_body
    review = await finalize_feedback(db, tenant_id, draft.review_id, text_to_finalize)
    draft.status = FeedbackDraftStatus.APPROVED_FINALIZED
    draft.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return draft, review


async def reject_feedback_draft(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID) -> FeedbackDraft | None:
    result = await db.execute(select(FeedbackDraft).where(FeedbackDraft.id == draft_id, FeedbackDraft.tenant_id == tenant_id))
    draft = result.scalar_one_or_none()
    if draft is None or draft.status != FeedbackDraftStatus.PENDING_REVIEW:
        return None
    draft.status = FeedbackDraftStatus.REJECTED
    draft.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return draft


async def create_nine_box_placement(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, review_cycle_id: uuid.UUID, performance_axis, potential_axis, reasoning: str = "") -> NineBoxPlacement:
    placement = NineBoxPlacement(tenant_id=tenant_id, employee_id=employee_id, review_cycle_id=review_cycle_id, performance_axis=performance_axis, potential_axis=potential_axis, reasoning=reasoning)
    db.add(placement)
    await db.commit()
    await db.refresh(placement)
    return placement


async def list_nine_box_grid(db: AsyncSession, tenant_id: str, review_cycle_id: uuid.UUID) -> list[NineBoxPlacement]:
    result = await db.execute(select(NineBoxPlacement).where(NineBoxPlacement.tenant_id == tenant_id, NineBoxPlacement.review_cycle_id == review_cycle_id))
    return list(result.scalars().all())


async def confirm_nine_box_placement(db: AsyncSession, tenant_id: str, placement_id: uuid.UUID) -> NineBoxPlacement:
    result = await db.execute(select(NineBoxPlacement).where(NineBoxPlacement.id == placement_id, NineBoxPlacement.tenant_id == tenant_id))
    placement = result.scalar_one_or_none()
    if placement is None or placement.status != NineBoxStatus.AI_SUGGESTED:
        raise NineBoxNotPendingError("placement not found or already confirmed")
    placement.status = NineBoxStatus.CONFIRMED
    placement.confirmed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(placement)
    return placement
