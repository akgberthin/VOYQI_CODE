"""
HRM-003 AI Talent CRM & Recruiting Pipeline.

submit_application publishes hrm.application_submitted unconditionally
— this is what makes resume screening AI-first: the Talent Agent scores
every application automatically, not on request. reject_application
publishes hrm.application_rejected, triggering candidate-nurture draft
generation the same way. Stage advancement past SCREENING is always a
human call — an interview outcome and an offer decision are things no
scoring heuristic observes. approve_and_send_nurture is the sole
human-confirmed action that turns a nurture draft into a real outbound
message, mirroring marketing-svc's ContentDraft and crm-svc's
DraftReply exactly.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    Application,
    ApplicationStage,
    CandidateInteraction,
    CandidateInteractionType,
    CandidateNurtureDraft,
    CandidateProfile,
    NurtureDraftStatus,
    Requisition,
    RequisitionStatus,
    SourceChannel,
)


class NurtureDraftNotPendingError(ValueError):
    pass


# ── Candidates ──────────────────────────────────────────────────────────

async def create_candidate(db: AsyncSession, tenant_id: str, full_name: str, email: str = "", phone: str = "", source_channel: SourceChannel = SourceChannel.DIRECT, resume_summary: str = "") -> CandidateProfile:
    candidate = CandidateProfile(tenant_id=tenant_id, full_name=full_name, email=email, phone=phone, source_channel=source_channel, resume_summary=resume_summary)
    db.add(candidate)
    await db.commit()
    await db.refresh(candidate)
    return candidate


async def get_candidate(db: AsyncSession, tenant_id: str, candidate_id: uuid.UUID) -> CandidateProfile | None:
    result = await db.execute(select(CandidateProfile).where(CandidateProfile.id == candidate_id, CandidateProfile.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_candidates(db: AsyncSession, tenant_id: str, source_channel: SourceChannel | None = None) -> list[CandidateProfile]:
    stmt = select(CandidateProfile).where(CandidateProfile.tenant_id == tenant_id)
    if source_channel:
        stmt = stmt.where(CandidateProfile.source_channel == source_channel)
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Requisitions ────────────────────────────────────────────────────────

async def create_requisition(db: AsyncSession, tenant_id: str, title: str, department: str = "", required_skills: str = "", position_id: uuid.UUID | None = None) -> Requisition:
    req = Requisition(tenant_id=tenant_id, title=title, department=department, required_skills=required_skills, position_id=position_id)
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return req


async def get_requisition(db: AsyncSession, tenant_id: str, requisition_id: uuid.UUID) -> Requisition | None:
    result = await db.execute(select(Requisition).where(Requisition.id == requisition_id, Requisition.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_requisitions(db: AsyncSession, tenant_id: str, status: RequisitionStatus | None = None) -> list[Requisition]:
    stmt = select(Requisition).where(Requisition.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(Requisition.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def close_requisition(db: AsyncSession, tenant_id: str, requisition_id: uuid.UUID, status: RequisitionStatus) -> Requisition | None:
    req = await get_requisition(db, tenant_id, requisition_id)
    if req is None:
        return None
    req.status = status
    await db.commit()
    await db.refresh(req)
    return req


# ── Applications (the pipeline) ─────────────────────────────────────────

async def submit_application(db: AsyncSession, tenant_id: str, candidate_id: uuid.UUID, requisition_id: uuid.UUID) -> Application:
    application = Application(tenant_id=tenant_id, candidate_id=candidate_id, requisition_id=requisition_id)
    db.add(application)
    db.add(CandidateInteraction(tenant_id=tenant_id, candidate_id=candidate_id, interaction_type=CandidateInteractionType.APPLICATION_SUBMITTED, summary=f"Applied to requisition {requisition_id}"))
    await db.commit()
    await db.refresh(application)
    return application


async def get_application(db: AsyncSession, tenant_id: str, application_id: uuid.UUID) -> Application | None:
    result = await db.execute(select(Application).where(Application.id == application_id, Application.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_applications(db: AsyncSession, tenant_id: str, requisition_id: uuid.UUID | None = None, stage: ApplicationStage | None = None) -> list[Application]:
    stmt = select(Application).where(Application.tenant_id == tenant_id)
    if requisition_id:
        stmt = stmt.where(Application.requisition_id == requisition_id)
    if stage:
        stmt = stmt.where(Application.stage == stage)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def apply_screening_score(db: AsyncSession, tenant_id: str, application_id: uuid.UUID, score: float, reasoning: str) -> Application | None:
    """The only function that ever sets Application.score — always
    from the Talent Agent's evaluation, always logged with reasoning."""
    application = await get_application(db, tenant_id, application_id)
    if application is None:
        return None
    application.score = score
    application.score_reasoning = reasoning
    if application.stage == ApplicationStage.APPLIED:
        application.stage = ApplicationStage.SCREENING
    await db.commit()
    await db.refresh(application)
    return application


async def advance_application_stage(db: AsyncSession, tenant_id: str, application_id: uuid.UUID, new_stage: ApplicationStage) -> Application | None:
    """A human call past SCREENING — interview outcomes and offer
    decisions aren't something a scoring heuristic observes."""
    application = await get_application(db, tenant_id, application_id)
    if application is None:
        return None
    application.stage = new_stage
    await db.commit()
    await db.refresh(application)
    return application


async def reject_application(db: AsyncSession, tenant_id: str, application_id: uuid.UUID, reason: str = "") -> Application | None:
    application = await advance_application_stage(db, tenant_id, application_id, ApplicationStage.REJECTED)
    if application is None:
        return None
    db.add(CandidateInteraction(tenant_id=tenant_id, candidate_id=application.candidate_id, interaction_type=CandidateInteractionType.REJECTION, summary=reason or "Application not advanced"))
    await db.commit()
    return application


# ── Candidate interaction timeline (facts only) ─────────────────────────

async def add_interaction(db: AsyncSession, tenant_id: str, candidate_id: uuid.UUID, interaction_type: CandidateInteractionType, summary: str = "") -> CandidateInteraction:
    interaction = CandidateInteraction(tenant_id=tenant_id, candidate_id=candidate_id, interaction_type=interaction_type, summary=summary)
    db.add(interaction)
    await db.commit()
    await db.refresh(interaction)
    return interaction


async def get_timeline(db: AsyncSession, tenant_id: str, candidate_id: uuid.UUID) -> list[CandidateInteraction]:
    result = await db.execute(
        select(CandidateInteraction).where(CandidateInteraction.tenant_id == tenant_id, CandidateInteraction.candidate_id == candidate_id).order_by(CandidateInteraction.occurred_at.desc())
    )
    return list(result.scalars().all())


# ── Nurture drafts (Talent Agent output, human-confirmed send) ────────

async def create_nurture_draft(db: AsyncSession, tenant_id: str, candidate_id: uuid.UUID, draft_body: str, reasoning: str = "", application_id: uuid.UUID | None = None) -> CandidateNurtureDraft:
    draft = CandidateNurtureDraft(tenant_id=tenant_id, candidate_id=candidate_id, application_id=application_id, draft_body=draft_body, reasoning=reasoning)
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def list_pending_nurture_drafts(db: AsyncSession, tenant_id: str, candidate_id: uuid.UUID | None = None) -> list[CandidateNurtureDraft]:
    stmt = select(CandidateNurtureDraft).where(CandidateNurtureDraft.tenant_id == tenant_id, CandidateNurtureDraft.status == NurtureDraftStatus.PENDING_REVIEW)
    if candidate_id:
        stmt = stmt.where(CandidateNurtureDraft.candidate_id == candidate_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def approve_and_send_nurture(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID, final_body: str | None = None) -> tuple[CandidateNurtureDraft, CandidateInteraction]:
    result = await db.execute(select(CandidateNurtureDraft).where(CandidateNurtureDraft.id == draft_id, CandidateNurtureDraft.tenant_id == tenant_id))
    draft = result.scalar_one_or_none()
    if draft is None or draft.status != NurtureDraftStatus.PENDING_REVIEW:
        raise NurtureDraftNotPendingError("draft not found or already reviewed")

    body_to_send = final_body if final_body is not None else draft.draft_body
    interaction = CandidateInteraction(tenant_id=tenant_id, candidate_id=draft.candidate_id, interaction_type=CandidateInteractionType.NURTURE_MESSAGE, summary=body_to_send)
    db.add(interaction)
    draft.status = NurtureDraftStatus.APPROVED_SENT
    draft.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    await db.refresh(interaction)
    return draft, interaction


async def reject_nurture_draft(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID) -> CandidateNurtureDraft | None:
    result = await db.execute(select(CandidateNurtureDraft).where(CandidateNurtureDraft.id == draft_id, CandidateNurtureDraft.tenant_id == tenant_id))
    draft = result.scalar_one_or_none()
    if draft is None or draft.status != NurtureDraftStatus.PENDING_REVIEW:
        return None
    draft.status = NurtureDraftStatus.REJECTED
    draft.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return draft


# ── Sourcing-channel analytics (deterministic aggregation) ────────────

async def get_sourcing_channel_analytics(db: AsyncSession, tenant_id: str) -> list[dict]:
    candidates = await list_candidates(db, tenant_id)
    by_channel: dict[str, dict] = {}
    for c in candidates:
        by_channel.setdefault(c.source_channel.value, {"candidate_count": 0, "hired_count": 0})
        by_channel[c.source_channel.value]["candidate_count"] += 1

    hired_result = await db.execute(select(Application).where(Application.tenant_id == tenant_id, Application.stage == ApplicationStage.HIRED))
    hired_apps = list(hired_result.scalars().all())
    for app in hired_apps:
        candidate = await get_candidate(db, tenant_id, app.candidate_id)
        if candidate:
            by_channel.setdefault(candidate.source_channel.value, {"candidate_count": 0, "hired_count": 0})
            by_channel[candidate.source_channel.value]["hired_count"] += 1

    return [
        {"source_channel": k, "candidate_count": v["candidate_count"], "hired_count": v["hired_count"],
         "hire_rate_pct": round((v["hired_count"] / v["candidate_count"]) * 100, 1) if v["candidate_count"] else 0.0}
        for k, v in by_channel.items()
    ]
