import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import (
    ApplicationStage,
    CandidateInteractionType,
    NurtureDraftStatus,
    RequisitionStatus,
    SourceChannel,
)
from src.services import recruiting_engine
from src.services.recruiting_engine import NurtureDraftNotPendingError

router = APIRouter(prefix="/api/v1/hrm", tags=["recruiting"])


# ── Schemas ───────────────────────────────────────────────────────────

class CandidateCreate(BaseModel):
    full_name: str
    email: str = ""
    phone: str = ""
    source_channel: SourceChannel = SourceChannel.DIRECT
    resume_summary: str = ""


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str
    phone: str
    source_channel: SourceChannel
    resume_summary: str
    created_at: datetime


class RequisitionCreate(BaseModel):
    title: str
    department: str = ""
    required_skills: str = ""
    position_id: uuid.UUID | None = None


class RequisitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    department: str
    required_skills: str
    position_id: uuid.UUID | None
    status: RequisitionStatus
    created_at: datetime


class ApplicationCreate(BaseModel):
    candidate_id: uuid.UUID
    requisition_id: uuid.UUID


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    requisition_id: uuid.UUID
    stage: ApplicationStage
    score: float | None
    score_reasoning: str
    created_at: datetime


class StageUpdate(BaseModel):
    stage: ApplicationStage


class RejectRequest(BaseModel):
    reason: str = ""


class InteractionCreate(BaseModel):
    interaction_type: CandidateInteractionType
    summary: str = ""


class InteractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    interaction_type: CandidateInteractionType
    summary: str
    occurred_at: datetime


class NurtureDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    application_id: uuid.UUID | None
    draft_body: str
    reasoning: str
    status: NurtureDraftStatus
    reviewed_at: datetime | None


class ApproveRequest(BaseModel):
    final_body: str | None = None


# ── Candidates ──────────────────────────────────────────────────────────

@router.post("/candidates", response_model=CandidateOut, status_code=201)
async def create_candidate(body: CandidateCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recruiting_engine.create_candidate(db, tenant_id, body.full_name, body.email, body.phone, body.source_channel, body.resume_summary)


@router.get("/candidates", response_model=list[CandidateOut])
async def list_candidates(source_channel: SourceChannel | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recruiting_engine.list_candidates(db, tenant_id, source_channel=source_channel)


@router.get("/candidates/{candidate_id}/timeline", response_model=list[InteractionOut])
async def get_timeline(candidate_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recruiting_engine.get_timeline(db, tenant_id, candidate_id)


@router.post("/candidates/{candidate_id}/interactions", response_model=InteractionOut, status_code=201)
async def add_interaction(candidate_id: uuid.UUID, body: InteractionCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recruiting_engine.add_interaction(db, tenant_id, candidate_id, body.interaction_type, body.summary)


# ── Requisitions ──────────────────────────────────────────────────────

@router.post("/requisitions", response_model=RequisitionOut, status_code=201)
async def create_requisition(body: RequisitionCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recruiting_engine.create_requisition(db, tenant_id, body.title, body.department, body.required_skills, body.position_id)


@router.get("/requisitions", response_model=list[RequisitionOut])
async def list_requisitions(status: RequisitionStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recruiting_engine.list_requisitions(db, tenant_id, status=status)


@router.post("/requisitions/{requisition_id}/close", response_model=RequisitionOut)
async def close_requisition(requisition_id: uuid.UUID, status: RequisitionStatus = RequisitionStatus.FILLED, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    req = await recruiting_engine.close_requisition(db, tenant_id, requisition_id, status)
    if req is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    return req


@router.get("/analytics/sourcing-channels")
async def get_sourcing_channel_analytics(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recruiting_engine.get_sourcing_channel_analytics(db, tenant_id)


# ── Applications — AI-first: submission triggers resume screening ────

@router.post("/applications", response_model=ApplicationOut, status_code=201)
async def submit_application(
    body: ApplicationCreate, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    candidate = await recruiting_engine.get_candidate(db, tenant_id, body.candidate_id)
    requisition = await recruiting_engine.get_requisition(db, tenant_id, body.requisition_id)
    if candidate is None or requisition is None:
        raise HTTPException(status_code=404, detail="Candidate or requisition not found")
    application = await recruiting_engine.submit_application(db, tenant_id, body.candidate_id, body.requisition_id)
    # AI-first: resume screening runs automatically, immediately —
    # not on request.
    background_tasks.add_task(
        publish_event,
        "hrm.recruiting",
        "hrm.application_submitted",
        tenant_id,
        {
            "application_id": str(application.id), "candidate_id": str(body.candidate_id), "requisition_id": str(body.requisition_id),
            "resume_summary": candidate.resume_summary, "required_skills": requisition.required_skills,
        },
    )
    return application


@router.post("/applications/{application_id}/screen-now")
async def screen_now(application_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation submission
    already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    application = await recruiting_engine.get_application(db, tenant_id, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    candidate = await recruiting_engine.get_candidate(db, tenant_id, application.candidate_id)
    requisition = await recruiting_engine.get_requisition(db, tenant_id, application.requisition_id)
    response = ai_orchestration_tools.request_screening(tenant_id, str(application_id), candidate.resume_summary if candidate else "", requisition.required_skills if requisition else "")
    if response is None:
        raise HTTPException(status_code=502, detail="Talent Agent unreachable")
    return response


@router.get("/applications", response_model=list[ApplicationOut])
async def list_applications(requisition_id: uuid.UUID | None = None, stage: ApplicationStage | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recruiting_engine.list_applications(db, tenant_id, requisition_id=requisition_id, stage=stage)


@router.get("/applications/{application_id}", response_model=ApplicationOut)
async def get_application(application_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    application = await recruiting_engine.get_application(db, tenant_id, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


@router.post("/applications/{application_id}/apply-screening-score", response_model=ApplicationOut)
async def apply_screening_score(application_id: uuid.UUID, score: float, reasoning: str = "", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    application = await recruiting_engine.apply_screening_score(db, tenant_id, application_id, score, reasoning)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


@router.post("/applications/{application_id}/advance-stage", response_model=ApplicationOut)
async def advance_application_stage(application_id: uuid.UUID, body: StageUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    application = await recruiting_engine.advance_application_stage(db, tenant_id, application_id, body.stage)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


@router.post("/applications/{application_id}/reject", response_model=ApplicationOut)
async def reject_application(
    application_id: uuid.UUID, body: RejectRequest, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    application = await recruiting_engine.reject_application(db, tenant_id, application_id, body.reason)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    candidate = await recruiting_engine.get_candidate(db, tenant_id, application.candidate_id)
    # AI-first: candidate-nurture drafting runs automatically on
    # rejection — a real "silver medalist" recruiting practice, not
    # held until a recruiter remembers to follow up.
    background_tasks.add_task(
        publish_event,
        "hrm.recruiting",
        "hrm.application_rejected",
        tenant_id,
        {"application_id": str(application_id), "candidate_id": str(application.candidate_id), "candidate_name": candidate.full_name if candidate else "", "reason": body.reason},
    )
    return application


# ── Nurture drafts ────────────────────────────────────────────────────

class NurtureDraftCreate(BaseModel):
    draft_body: str
    reasoning: str = ""
    application_id: uuid.UUID | None = None


@router.post("/candidates/{candidate_id}/nurture-drafts", response_model=NurtureDraftOut, status_code=201)
async def create_nurture_draft(candidate_id: uuid.UUID, body: NurtureDraftCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recruiting_engine.create_nurture_draft(db, tenant_id, candidate_id, body.draft_body, body.reasoning, body.application_id)


@router.get("/candidates/{candidate_id}/nurture-drafts/pending", response_model=list[NurtureDraftOut])
async def list_pending_nurture_drafts(candidate_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recruiting_engine.list_pending_nurture_drafts(db, tenant_id, candidate_id)


@router.post("/nurture-drafts/{draft_id}/approve-and-send")
async def approve_and_send_nurture(draft_id: uuid.UUID, body: ApproveRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        draft, interaction = await recruiting_engine.approve_and_send_nurture(db, tenant_id, draft_id, body.final_body)
    except NurtureDraftNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"draft_id": str(draft.id), "interaction_id": str(interaction.id), "body": interaction.summary}


@router.post("/nurture-drafts/{draft_id}/reject", response_model=NurtureDraftOut)
async def reject_nurture_draft(draft_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    draft = await recruiting_engine.reject_nurture_draft(db, tenant_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=409, detail="Draft not found or already reviewed")
    return draft
