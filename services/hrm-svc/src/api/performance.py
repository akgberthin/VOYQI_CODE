import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import (
    FeedbackDraftStatus,
    GoalStatus,
    NineBoxStatus,
    ReviewCycleStatus,
    ReviewStatus,
)
from src.services import performance_engine
from src.services.performance_engine import (
    FeedbackDraftNotPendingError,
    NineBoxNotPendingError,
)

router = APIRouter(prefix="/api/v1/hrm", tags=["performance"])


class GoalCreate(BaseModel):
    title: str
    description: str = ""
    target_date: datetime | None = None


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    title: str
    description: str
    target_date: datetime | None
    status: GoalStatus


class ReviewCycleCreate(BaseModel):
    name: str
    start_date: datetime
    end_date: datetime


class ReviewCycleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    start_date: datetime
    end_date: datetime
    status: ReviewCycleStatus


class ReviewCreate(BaseModel):
    employee_id: uuid.UUID
    review_cycle_id: uuid.UUID
    rating: float | None = None
    manager_notes: str = ""
    reviewer_id: uuid.UUID | None = None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    review_cycle_id: uuid.UUID
    reviewer_id: uuid.UUID | None
    rating: float | None
    manager_notes: str
    finalized_feedback: str
    status: ReviewStatus


class FeedbackDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    review_id: uuid.UUID
    draft_body: str
    reasoning: str
    status: FeedbackDraftStatus
    reviewed_at: datetime | None


class ApproveRequest(BaseModel):
    final_text: str | None = None


class NineBoxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    review_cycle_id: uuid.UUID
    performance_axis: str
    potential_axis: str
    reasoning: str
    status: NineBoxStatus
    confirmed_at: datetime | None


@router.post("/employees/{employee_id}/goals", response_model=GoalOut, status_code=201)
async def create_goal(employee_id: uuid.UUID, body: GoalCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await performance_engine.create_goal(db, tenant_id, employee_id, body.title, body.description, body.target_date)


@router.get("/employees/{employee_id}/goals", response_model=list[GoalOut])
async def list_goals(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await performance_engine.list_goals(db, tenant_id, employee_id)


@router.post("/goals/{goal_id}/status")
async def update_goal_status(goal_id: uuid.UUID, status: GoalStatus, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    goal = await performance_engine.update_goal_status(db, tenant_id, goal_id, status)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return {"id": str(goal.id), "status": goal.status.value}


@router.get("/employees/{employee_id}/goal-completion-rate")
async def get_goal_completion_rate(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    rate = await performance_engine.get_goal_completion_rate(db, tenant_id, employee_id)
    return {"employee_id": str(employee_id), "completion_rate": rate}


@router.post("/review-cycles", response_model=ReviewCycleOut, status_code=201)
async def create_review_cycle(body: ReviewCycleCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await performance_engine.create_review_cycle(db, tenant_id, body.name, body.start_date, body.end_date)


@router.post("/review-cycles/{cycle_id}/close", response_model=ReviewCycleOut)
async def close_review_cycle(
    cycle_id: uuid.UUID, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    cycle = await performance_engine.close_review_cycle(db, tenant_id, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Review cycle not found")
    reviews = await performance_engine.list_reviews_for_cycle(db, tenant_id, cycle_id)
    for review in reviews:
        completion_rate = await performance_engine.get_goal_completion_rate(db, tenant_id, review.employee_id)
        background_tasks.add_task(
            publish_event,
            "hrm.performance",
            "hrm.nine_box_evaluation_requested",
            tenant_id,
            {"employee_id": str(review.employee_id), "review_cycle_id": str(cycle_id), "rating": review.rating, "goal_completion_rate": completion_rate},
        )
    return cycle


@router.post("/reviews", response_model=ReviewOut, status_code=201)
async def create_review(body: ReviewCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await performance_engine.create_review(db, tenant_id, body.employee_id, body.review_cycle_id, body.rating, body.manager_notes, body.reviewer_id)


@router.get("/review-cycles/{cycle_id}/reviews", response_model=list[ReviewOut])
async def list_reviews_for_cycle(cycle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await performance_engine.list_reviews_for_cycle(db, tenant_id, cycle_id)


@router.post("/reviews/{review_id}/request-feedback-draft", status_code=202)
async def request_feedback_draft(
    review_id: uuid.UUID, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    review = await performance_engine.get_review(db, tenant_id, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    background_tasks.add_task(
        publish_event,
        "hrm.performance",
        "hrm.feedback_draft_requested",
        tenant_id,
        {"review_id": str(review_id), "manager_notes": review.manager_notes, "rating": review.rating},
    )
    return {"review_id": str(review_id), "status": "draft_requested"}


@router.post("/reviews/{review_id}/draft-feedback-now")
async def draft_feedback_now(review_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation
    request-feedback-draft already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    review = await performance_engine.get_review(db, tenant_id, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    response = ai_orchestration_tools.request_feedback_draft(tenant_id, str(review_id), review.manager_notes, review.rating)
    if response is None:
        raise HTTPException(status_code=502, detail="Talent Agent unreachable")
    return response


class FeedbackDraftCreate(BaseModel):
    draft_body: str
    reasoning: str = ""


@router.post("/reviews/{review_id}/feedback-drafts", response_model=FeedbackDraftOut, status_code=201)
async def create_feedback_draft(review_id: uuid.UUID, body: FeedbackDraftCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await performance_engine.create_feedback_draft(db, tenant_id, review_id, body.draft_body, body.reasoning)


@router.get("/reviews/{review_id}/feedback-drafts/pending", response_model=list[FeedbackDraftOut])
async def list_pending_feedback_drafts(review_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await performance_engine.list_pending_feedback_drafts(db, tenant_id, review_id)


@router.post("/feedback-drafts/{draft_id}/approve")
async def approve_feedback_draft(draft_id: uuid.UUID, body: ApproveRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        draft, review = await performance_engine.approve_feedback_draft(db, tenant_id, draft_id, body.final_text)
    except FeedbackDraftNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"draft_id": str(draft.id), "review_id": str(review.id), "finalized_feedback": review.finalized_feedback}


@router.post("/feedback-drafts/{draft_id}/reject", response_model=FeedbackDraftOut)
async def reject_feedback_draft(draft_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    draft = await performance_engine.reject_feedback_draft(db, tenant_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=409, detail="Draft not found or already reviewed")
    return draft


class NineBoxCreate(BaseModel):
    employee_id: uuid.UUID
    review_cycle_id: uuid.UUID
    performance_axis: str
    potential_axis: str
    reasoning: str = ""


@router.post("/nine-box-placements", response_model=NineBoxOut, status_code=201)
async def create_nine_box_placement(body: NineBoxCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await performance_engine.create_nine_box_placement(db, tenant_id, body.employee_id, body.review_cycle_id, body.performance_axis, body.potential_axis, body.reasoning)


@router.get("/review-cycles/{cycle_id}/nine-box-grid", response_model=list[NineBoxOut])
async def list_nine_box_grid(cycle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await performance_engine.list_nine_box_grid(db, tenant_id, cycle_id)


@router.post("/nine-box-placements/{placement_id}/confirm")
async def confirm_nine_box_placement(placement_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        placement = await performance_engine.confirm_nine_box_placement(db, tenant_id, placement_id)
    except NineBoxNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": str(placement.id), "status": placement.status.value}
