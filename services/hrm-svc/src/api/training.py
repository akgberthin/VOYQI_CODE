import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import (
    CourseCategory,
    CourseCompletionStatus,
    CourseContentType,
    CourseDraftStatus,
    CourseRecommendationStatus,
)
from src.services import employee_engine, training_engine
from src.services.training_engine import (
    CourseDraftNotPendingError,
    RecommendationNotPendingError,
)

router = APIRouter(prefix="/api/v1/hrm", tags=["training"])


class CourseCreate(BaseModel):
    title: str
    category: CourseCategory
    duration_hours: float = 1.0
    is_mandatory: bool = False
    department: str = ""
    content_type: CourseContentType = CourseContentType.DOCUMENT
    content_url: str = ""
    description: str = ""


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: CourseCategory
    duration_hours: float
    is_mandatory: bool
    department: str
    content_type: CourseContentType
    content_url: str
    description: str


class CompletionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    course_id: uuid.UUID
    status: CourseCompletionStatus
    completed_at: datetime | None


class CovenantAckCreate(BaseModel):
    covenant_version: str


class CovenantAckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    covenant_version: str
    acknowledged_at: datetime


class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    recommended_course_id: uuid.UUID | None
    reasoning: str
    status: CourseRecommendationStatus


@router.post("/courses", response_model=CourseOut, status_code=201)
async def create_course(body: CourseCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await training_engine.create_course(
        db, tenant_id, body.title, body.category, body.duration_hours, body.is_mandatory, body.department,
        body.content_type, body.content_url, body.description,
    )


@router.get("/courses", response_model=list[CourseOut])
async def list_courses(department: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await training_engine.list_courses(db, tenant_id, department)


@router.post("/employees/{employee_id}/course-completions", response_model=CompletionOut, status_code=201)
async def enroll_course(employee_id: uuid.UUID, course_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await training_engine.enroll_course(db, tenant_id, employee_id, course_id)


@router.post("/course-completions/{completion_id}/complete", response_model=CompletionOut)
async def complete_course(completion_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    completion = await training_engine.complete_course(db, tenant_id, completion_id)
    if completion is None:
        raise HTTPException(status_code=404, detail="Completion not found")
    return completion


@router.get("/employees/{employee_id}/course-completions", response_model=list[CompletionOut])
async def list_completions_for_employee(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await training_engine.list_completions_for_employee(db, tenant_id, employee_id)


@router.post("/employees/{employee_id}/covenant-acknowledgments", response_model=CovenantAckOut, status_code=201)
async def acknowledge_covenant(employee_id: uuid.UUID, body: CovenantAckCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await training_engine.acknowledge_covenant(db, tenant_id, employee_id, body.covenant_version)


@router.get("/employees/{employee_id}/covenant-acknowledgments", response_model=list[CovenantAckOut])
async def list_covenant_acknowledgments(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await training_engine.list_covenant_acknowledgments(db, tenant_id, employee_id)


@router.post("/employees/{employee_id}/request-course-recommendation", status_code=202)
async def request_course_recommendation(
    employee_id: uuid.UUID, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    employee = await employee_engine.get_employee(db, tenant_id, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    incomplete = await training_engine.get_incomplete_mandatory_courses(db, tenant_id, employee_id, employee.department)
    # AI-first: recommendation runs automatically once requested — the
    # employee/manager never has to manually cross-reference the
    # catalog against completion records themselves.
    background_tasks.add_task(
        publish_event,
        "hrm.training",
        "hrm.course_recommendation_requested",
        tenant_id,
        {"employee_id": str(employee_id), "incomplete_mandatory_course_ids": [str(c.id) for c in incomplete], "incomplete_mandatory_course_titles": [c.title for c in incomplete]},
    )
    return {"employee_id": str(employee_id), "status": "recommendation_requested"}


@router.post("/employees/{employee_id}/recommend-course-now")
async def recommend_course_now(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation the request
    endpoint already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    employee = await employee_engine.get_employee(db, tenant_id, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    incomplete = await training_engine.get_incomplete_mandatory_courses(db, tenant_id, employee_id, employee.department)
    response = ai_orchestration_tools.request_course_recommendation(tenant_id, str(employee_id), [str(c.id) for c in incomplete], [c.title for c in incomplete])
    if response is None:
        raise HTTPException(status_code=502, detail="Talent Agent unreachable")
    return response


class RecommendationCreate(BaseModel):
    recommended_course_id: uuid.UUID | None = None
    reasoning: str = ""


@router.post("/employees/{employee_id}/course-recommendations", response_model=RecommendationOut, status_code=201)
async def create_course_recommendation(employee_id: uuid.UUID, body: RecommendationCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await training_engine.create_course_recommendation(db, tenant_id, employee_id, body.recommended_course_id, body.reasoning)


@router.get("/employees/{employee_id}/course-recommendations/pending", response_model=list[RecommendationOut])
async def list_pending_recommendations(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await training_engine.list_pending_recommendations(db, tenant_id, employee_id)


class AcceptRecommendationResponse(BaseModel):
    recommendation_id: str
    status: str
    completion_id: str | None


@router.post("/course-recommendations/{recommendation_id}/accept")
async def accept_recommendation(recommendation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        rec, completion = await training_engine.accept_recommendation(db, tenant_id, recommendation_id)
    except RecommendationNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"recommendation_id": str(rec.id), "status": rec.status.value, "completion_id": str(completion.id) if completion else None}


@router.post("/course-recommendations/{recommendation_id}/reject", response_model=RecommendationOut)
async def reject_recommendation(recommendation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    rec = await training_engine.reject_recommendation(db, tenant_id, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=409, detail="Recommendation not found or already reviewed")
    return rec


class CourseDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    prompt: str
    draft_title: str
    draft_category: CourseCategory
    draft_description: str
    draft_content_type: CourseContentType
    reasoning: str
    status: CourseDraftStatus


class CourseDraftCreate(BaseModel):
    prompt: str
    draft_title: str
    draft_category: CourseCategory
    draft_description: str = ""
    draft_content_type: CourseContentType = CourseContentType.DOCUMENT
    reasoning: str = ""


@router.post("/course-drafts", response_model=CourseDraftOut, status_code=201)
async def create_course_draft(body: CourseDraftCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await training_engine.create_course_draft(
        db, tenant_id, body.prompt, body.draft_title, body.draft_category, body.draft_description, body.draft_content_type, body.reasoning,
    )


@router.get("/course-drafts/pending", response_model=list[CourseDraftOut])
async def list_pending_course_drafts(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await training_engine.list_pending_course_drafts(db, tenant_id)


class CourseDraftPublish(BaseModel):
    final_title: str | None = None
    final_description: str | None = None
    is_mandatory: bool = False
    department: str = ""
    duration_hours: float = 1.0


@router.post("/course-drafts/{draft_id}/publish")
async def publish_course_draft(draft_id: uuid.UUID, body: CourseDraftPublish, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        draft, course = await training_engine.publish_course_draft(
            db, tenant_id, draft_id, body.final_title, body.final_description, body.is_mandatory, body.department, body.duration_hours,
        )
    except CourseDraftNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"draft_id": str(draft.id), "status": draft.status.value, "course_id": str(course.id)}


@router.post("/course-drafts/{draft_id}/reject", response_model=CourseDraftOut)
async def reject_course_draft(draft_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    draft = await training_engine.reject_course_draft(db, tenant_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=409, detail="Draft not found or already reviewed")
    return draft


@router.post("/employees/{employee_id}/request-course-draft", status_code=202)
async def request_course_draft(employee_id: uuid.UUID, prompt: str, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Not employee-scoped in effect — course drafting is a catalog-wide
    action — but routed through an HR-initiated action the same way
    every other AI-first trigger on this platform is, so there's a
    real endpoint an administrator actually calls."""
    background_tasks.add_task(
        publish_event,
        "hrm.training",
        "hrm.course_draft_requested",
        tenant_id,
        {"prompt": prompt},
    )
    return {"status": "course_draft_requested"}


@router.post("/course-drafts/draft-now")
async def draft_course_now(prompt: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation the
    request endpoint already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    response = ai_orchestration_tools.request_course_draft(tenant_id, prompt)
    if response is None:
        raise HTTPException(status_code=502, detail="Talent Agent unreachable")
    return response
