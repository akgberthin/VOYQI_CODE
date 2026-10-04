"""
HRM-006 Training & Certification (VOYQI Academy).

request_course_recommendation publishes hrm.course_recommendation_requested
— the Talent Agent recommends the next course automatically, but
create_course_recommendation always writes PENDING; accept_recommendation
is the only human-confirmed action that actually enrolls someone
(creates a CourseCompletion). Course catalog, completions, and covenant
acknowledgments are all deterministic facts — nothing here is AI-authored.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    Course,
    CourseCategory,
    CourseCompletion,
    CourseCompletionStatus,
    CourseContentType,
    CourseDraft,
    CourseDraftStatus,
    CourseRecommendation,
    CourseRecommendationStatus,
    CovenantAcknowledgment,
)


class RecommendationNotPendingError(ValueError):
    pass


class CourseDraftNotPendingError(ValueError):
    pass


# ── Course catalog (deterministic) ──────────────────────────────────────

async def create_course(
    db: AsyncSession, tenant_id: str, title: str, category: CourseCategory, duration_hours: float = 1.0, is_mandatory: bool = False, department: str = "",
    content_type: CourseContentType = CourseContentType.DOCUMENT, content_url: str = "", description: str = "",
) -> Course:
    course = Course(
        tenant_id=tenant_id, title=title, category=category, duration_hours=duration_hours, is_mandatory=is_mandatory, department=department,
        content_type=content_type, content_url=content_url, description=description,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return course


async def list_courses(db: AsyncSession, tenant_id: str, department: str | None = None) -> list[Course]:
    stmt = select(Course).where(Course.tenant_id == tenant_id)
    if department:
        stmt = stmt.where((Course.department == department) | (Course.department == ""))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_course(db: AsyncSession, tenant_id: str, course_id: uuid.UUID) -> Course | None:
    result = await db.execute(select(Course).where(Course.id == course_id, Course.tenant_id == tenant_id))
    return result.scalar_one_or_none()


# ── Course completions ───────────────────────────────────────────────────

async def enroll_course(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, course_id: uuid.UUID) -> CourseCompletion:
    completion = CourseCompletion(tenant_id=tenant_id, employee_id=employee_id, course_id=course_id)
    db.add(completion)
    await db.commit()
    await db.refresh(completion)
    return completion


async def complete_course(db: AsyncSession, tenant_id: str, completion_id: uuid.UUID) -> CourseCompletion | None:
    result = await db.execute(select(CourseCompletion).where(CourseCompletion.id == completion_id, CourseCompletion.tenant_id == tenant_id))
    completion = result.scalar_one_or_none()
    if completion is None:
        return None
    completion.status = CourseCompletionStatus.COMPLETED
    completion.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(completion)
    return completion


async def list_completions_for_employee(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[CourseCompletion]:
    result = await db.execute(select(CourseCompletion).where(CourseCompletion.tenant_id == tenant_id, CourseCompletion.employee_id == employee_id))
    return list(result.scalars().all())


async def get_incomplete_mandatory_courses(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, department: str) -> list[Course]:
    """Real, deterministic gap-finding: every mandatory course for the
    employee's department (or company-wide) they haven't completed
    yet — the input the Talent Agent's recommendation heuristic uses."""
    all_courses = await list_courses(db, tenant_id, department)
    mandatory = [c for c in all_courses if c.is_mandatory]
    completions = await list_completions_for_employee(db, tenant_id, employee_id)
    completed_course_ids = {c.course_id for c in completions if c.status == CourseCompletionStatus.COMPLETED}
    return [c for c in mandatory if c.id not in completed_course_ids]


# ── Covenant acknowledgments (deterministic signed fact) ───────────────

async def acknowledge_covenant(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, covenant_version: str) -> CovenantAcknowledgment:
    ack = CovenantAcknowledgment(tenant_id=tenant_id, employee_id=employee_id, covenant_version=covenant_version)
    db.add(ack)
    await db.commit()
    await db.refresh(ack)
    return ack


async def list_covenant_acknowledgments(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[CovenantAcknowledgment]:
    result = await db.execute(select(CovenantAcknowledgment).where(CovenantAcknowledgment.tenant_id == tenant_id, CovenantAcknowledgment.employee_id == employee_id))
    return list(result.scalars().all())


# ── Course recommendations (Talent Agent output, human-confirmed) ─────

async def create_course_recommendation(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, recommended_course_id: uuid.UUID | None, reasoning: str = "") -> CourseRecommendation:
    rec = CourseRecommendation(tenant_id=tenant_id, employee_id=employee_id, recommended_course_id=recommended_course_id, reasoning=reasoning)
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return rec


async def list_pending_recommendations(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID | None = None) -> list[CourseRecommendation]:
    stmt = select(CourseRecommendation).where(CourseRecommendation.tenant_id == tenant_id, CourseRecommendation.status == CourseRecommendationStatus.PENDING)
    if employee_id:
        stmt = stmt.where(CourseRecommendation.employee_id == employee_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def accept_recommendation(db: AsyncSession, tenant_id: str, recommendation_id: uuid.UUID) -> tuple[CourseRecommendation, CourseCompletion | None]:
    """The only human-confirmed action that turns a recommendation
    into an actual enrollment."""
    result = await db.execute(select(CourseRecommendation).where(CourseRecommendation.id == recommendation_id, CourseRecommendation.tenant_id == tenant_id))
    rec = result.scalar_one_or_none()
    if rec is None or rec.status != CourseRecommendationStatus.PENDING:
        raise RecommendationNotPendingError("recommendation not found or already reviewed")

    completion = None
    if rec.recommended_course_id:
        completion = await enroll_course(db, tenant_id, rec.employee_id, rec.recommended_course_id)
    rec.status = CourseRecommendationStatus.ACCEPTED
    await db.commit()
    await db.refresh(rec)
    return rec, completion


async def reject_recommendation(db: AsyncSession, tenant_id: str, recommendation_id: uuid.UUID) -> CourseRecommendation | None:
    result = await db.execute(select(CourseRecommendation).where(CourseRecommendation.id == recommendation_id, CourseRecommendation.tenant_id == tenant_id))
    rec = result.scalar_one_or_none()
    if rec is None or rec.status != CourseRecommendationStatus.PENDING:
        return None
    rec.status = CourseRecommendationStatus.REJECTED
    await db.commit()
    await db.refresh(rec)
    return rec


# ── AI-assisted course drafting (Talent Agent output, human publishes) ─

async def create_course_draft(
    db: AsyncSession, tenant_id: str, prompt: str, draft_title: str, draft_category: CourseCategory,
    draft_description: str = "", draft_content_type: CourseContentType = CourseContentType.DOCUMENT, reasoning: str = "",
) -> CourseDraft:
    draft = CourseDraft(
        tenant_id=tenant_id, prompt=prompt, draft_title=draft_title, draft_category=draft_category,
        draft_description=draft_description, draft_content_type=draft_content_type, reasoning=reasoning,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def list_pending_course_drafts(db: AsyncSession, tenant_id: str) -> list[CourseDraft]:
    result = await db.execute(select(CourseDraft).where(CourseDraft.tenant_id == tenant_id, CourseDraft.status == CourseDraftStatus.PENDING_REVIEW))
    return list(result.scalars().all())


async def publish_course_draft(
    db: AsyncSession, tenant_id: str, draft_id: uuid.UUID, final_title: str | None = None, final_description: str | None = None,
    is_mandatory: bool = False, department: str = "", duration_hours: float = 1.0,
) -> tuple[CourseDraft, Course]:
    """The only human-confirmed action that turns a course draft into
    a real catalog entry employees can be assigned to."""
    result = await db.execute(select(CourseDraft).where(CourseDraft.id == draft_id, CourseDraft.tenant_id == tenant_id))
    draft = result.scalar_one_or_none()
    if draft is None or draft.status != CourseDraftStatus.PENDING_REVIEW:
        raise CourseDraftNotPendingError("draft not found or already reviewed")

    course = await create_course(
        db, tenant_id, final_title or draft.draft_title, draft.draft_category, duration_hours, is_mandatory, department,
        draft.draft_content_type, "", final_description if final_description is not None else draft.draft_description,
    )
    draft.status = CourseDraftStatus.PUBLISHED
    await db.commit()
    await db.refresh(draft)
    return draft, course


async def reject_course_draft(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID) -> CourseDraft | None:
    result = await db.execute(select(CourseDraft).where(CourseDraft.id == draft_id, CourseDraft.tenant_id == tenant_id))
    draft = result.scalar_one_or_none()
    if draft is None or draft.status != CourseDraftStatus.PENDING_REVIEW:
        return None
    draft.status = CourseDraftStatus.REJECTED
    await db.commit()
    await db.refresh(draft)
    return draft
