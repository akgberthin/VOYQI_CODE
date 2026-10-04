from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import talent_agent

router = APIRouter(prefix="/api/v1/ai/hrm", tags=["talent-agent"])


class ChecklistPopulationRequest(BaseModel):
    checklist_id: str
    kind: str
    job_title: str
    department: str
    entity_ref: str = ""


@router.post("/evaluate-checklist-population")
async def evaluate_checklist_population(body: ChecklistPopulationRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_checklist_population(
        db, tenant_id=tenant_id, checklist_id=body.checklist_id, kind=body.kind, job_title=body.job_title, department=body.department, entity_ref=body.entity_ref,
    )
    return {
        "tasks_added": result.tasks_added,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class BenefitsRecommendationRequest(BaseModel):
    employee_id: str
    job_title: str
    department: str


@router.post("/evaluate-benefits-recommendation")
async def evaluate_benefits_recommendation(body: BenefitsRecommendationRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_benefits_recommendation(
        db, tenant_id=tenant_id, employee_id=body.employee_id, job_title=body.job_title, department=body.department,
    )
    return {
        "recommended_plan_id": result.recommended_plan_id,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class ScreeningRequest(BaseModel):
    application_id: str
    resume_summary: str
    required_skills: str


@router.post("/evaluate-screening")
async def evaluate_screening(body: ScreeningRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_application_screening(
        db, tenant_id=tenant_id, application_id=body.application_id, resume_summary=body.resume_summary, required_skills=body.required_skills,
    )
    return {
        "score": result.score,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class NurtureRequest(BaseModel):
    candidate_id: str
    application_id: str = ""
    candidate_name: str
    reason: str = ""


@router.post("/evaluate-nurture")
async def evaluate_nurture(body: NurtureRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_candidate_nurture(
        db, tenant_id=tenant_id, candidate_id=body.candidate_id, application_id=body.application_id, candidate_name=body.candidate_name, reason=body.reason,
    )
    return {
        "draft_created": result.draft_created,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class FeedbackDraftRequest(BaseModel):
    review_id: str
    manager_notes: str
    rating: float | None = None


@router.post("/evaluate-feedback-draft")
async def evaluate_feedback_draft(body: FeedbackDraftRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_feedback_draft(db, tenant_id=tenant_id, review_id=body.review_id, manager_notes=body.manager_notes, rating=body.rating)
    return {
        "draft_created": result.draft_created,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class NineBoxRequest(BaseModel):
    employee_id: str
    review_cycle_id: str
    rating: float | None = None
    goal_completion_rate: float | None = None


@router.post("/evaluate-nine-box")
async def evaluate_nine_box(body: NineBoxRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_nine_box_placement(
        db, tenant_id=tenant_id, employee_id=body.employee_id, review_cycle_id=body.review_cycle_id, rating=body.rating, goal_completion_rate=body.goal_completion_rate,
    )
    return {
        "placement_created": result.placement_created,
        "performance_axis": result.performance_axis,
        "potential_axis": result.potential_axis,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class AttritionRiskRequest(BaseModel):
    employee_id: str
    tenure_months: int | None = None
    comp_band_position: float | None = None
    months_since_last_comp_change: int | None = None
    goal_completion_rate: float | None = None


@router.post("/evaluate-attrition-risk")
async def evaluate_attrition_risk(body: AttritionRiskRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_attrition_risk(
        db, tenant_id=tenant_id, employee_id=body.employee_id, tenure_months=body.tenure_months, comp_band_position=body.comp_band_position,
        months_since_last_comp_change=body.months_since_last_comp_change, goal_completion_rate=body.goal_completion_rate,
    )
    return {
        "risk_score": result.risk_score,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class CourseRecommendationRequest(BaseModel):
    employee_id: str
    incomplete_mandatory_course_ids: list[str] = []
    incomplete_mandatory_course_titles: list[str] = []


@router.post("/evaluate-course-recommendation")
async def evaluate_course_recommendation(body: CourseRecommendationRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_course_recommendation(
        db, tenant_id=tenant_id, employee_id=body.employee_id, incomplete_mandatory_course_ids=body.incomplete_mandatory_course_ids,
        incomplete_mandatory_course_titles=body.incomplete_mandatory_course_titles,
    )
    return {
        "recommendation_created": result.recommendation_created,
        "recommended_course_id": result.recommended_course_id,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class PolicyQuestionRequest(BaseModel):
    case_id: str
    question: str


@router.post("/evaluate-policy-question")
async def evaluate_policy_question(body: PolicyQuestionRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_policy_question(db, tenant_id=tenant_id, case_id=body.case_id, question=body.question)
    return {
        "draft_created": result.draft_created,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class NoteUpdateRequest(BaseModel):
    employee_id: str
    context: str


@router.post("/evaluate-note-update")
async def evaluate_note_update(body: NoteUpdateRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_note_update(db, tenant_id=tenant_id, employee_id=body.employee_id, context=body.context)
    return {
        "note_written": result.note_written,
        "action_taken": result.action_taken,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class EmailDraftRequest(BaseModel):
    recipient_scope: str
    prompt: str
    recipient_employee_id: str | None = None


@router.post("/evaluate-email-draft")
async def evaluate_email_draft(body: EmailDraftRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_email_draft(
        db, tenant_id=tenant_id, recipient_scope=body.recipient_scope, prompt=body.prompt, recipient_employee_id=body.recipient_employee_id,
    )
    return {
        "email_created": result.email_created,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class CourseDraftRequest(BaseModel):
    prompt: str


@router.post("/evaluate-course-draft")
async def evaluate_course_draft(body: CourseDraftRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await talent_agent.evaluate_course_draft(db, tenant_id=tenant_id, prompt=body.prompt)
    return {
        "draft_created": result.draft_created,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }
