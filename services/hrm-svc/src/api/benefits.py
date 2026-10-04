import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import (
    BenefitsPlanType,
    BenefitsRecommendationStatus,
    BenefitsTier,
    EnrollmentStatus,
)
from src.services import benefits_engine, employee_engine
from src.services.benefits_engine import RecommendationNotPendingError

router = APIRouter(prefix="/api/v1/hrm", tags=["benefits"])


class PlanCreate(BaseModel):
    name: str
    plan_type: BenefitsPlanType
    tier: BenefitsTier
    monthly_premium_usd: float
    employer_contribution_pct: float = 0.0


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    plan_type: BenefitsPlanType
    tier: BenefitsTier
    monthly_premium_usd: float
    employer_contribution_pct: float


class EnrollmentCreate(BaseModel):
    plan_id: uuid.UUID


class EnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    plan_id: uuid.UUID
    status: EnrollmentStatus
    enrolled_at: datetime


class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    recommended_plan_id: uuid.UUID
    reasoning: str
    status: BenefitsRecommendationStatus
    created_at: datetime


@router.post("/benefits-plans", response_model=PlanOut, status_code=201)
async def create_plan(body: PlanCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await benefits_engine.create_plan(db, tenant_id, body.name, body.plan_type, body.tier, body.monthly_premium_usd, body.employer_contribution_pct)


@router.get("/benefits-plans", response_model=list[PlanOut])
async def list_plans(plan_type: BenefitsPlanType | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await benefits_engine.list_plans(db, tenant_id, plan_type=plan_type)


@router.post("/employees/{employee_id}/benefits-enrollments", response_model=EnrollmentOut, status_code=201)
async def enroll(employee_id: uuid.UUID, body: EnrollmentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await benefits_engine.enroll(db, tenant_id, employee_id, body.plan_id)


@router.get("/employees/{employee_id}/benefits-enrollments", response_model=list[EnrollmentOut])
async def list_enrollments(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await benefits_engine.list_enrollments(db, tenant_id, employee_id)


@router.post("/benefits-enrollments/{enrollment_id}/terminate", response_model=EnrollmentOut)
async def terminate_enrollment(enrollment_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    enrollment = await benefits_engine.terminate_enrollment(db, tenant_id, enrollment_id)
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    return enrollment


@router.post("/employees/{employee_id}/request-benefits-recommendation", status_code=202)
async def request_benefits_recommendation(
    employee_id: uuid.UUID, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    employee = await employee_engine.get_employee(db, tenant_id, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    # AI-first: the recommendation is evaluated the instant enrollment
    # is initiated (hire or open enrollment) — not on a separate manual ask.
    background_tasks.add_task(
        publish_event,
        "hrm.benefits",
        "hrm.benefits_recommendation_requested",
        tenant_id,
        {"employee_id": str(employee_id), "job_title": employee.job_title, "department": employee.department},
    )
    return {"employee_id": str(employee_id), "status": "recommendation_requested"}


class RecommendationCreate(BaseModel):
    recommended_plan_id: uuid.UUID
    reasoning: str = ""


@router.post("/employees/{employee_id}/benefits-recommendations", response_model=RecommendationOut, status_code=201)
async def create_recommendation(employee_id: uuid.UUID, body: RecommendationCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await benefits_engine.create_recommendation(db, tenant_id, employee_id, body.recommended_plan_id, body.reasoning)


@router.get("/employees/{employee_id}/benefits-recommendations/pending", response_model=list[RecommendationOut])
async def list_pending_recommendations(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await benefits_engine.list_pending_recommendations(db, tenant_id, employee_id)


@router.post("/benefits-recommendations/{recommendation_id}/accept")
async def accept_recommendation(recommendation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        rec, enrollment = await benefits_engine.accept_recommendation(db, tenant_id, recommendation_id)
    except RecommendationNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"recommendation_id": str(rec.id), "enrollment_id": str(enrollment.id), "plan_id": str(enrollment.plan_id)}


@router.post("/benefits-recommendations/{recommendation_id}/reject", response_model=RecommendationOut)
async def reject_recommendation(recommendation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    rec = await benefits_engine.reject_recommendation(db, tenant_id, recommendation_id)
    if rec is None:
        raise HTTPException(status_code=409, detail="Recommendation not found or already reviewed")
    return rec


@router.post("/employees/{employee_id}/benefits-recommendation-now")
async def request_benefits_recommendation_now(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation the async
    request already triggers."""
    from src.services import ai_orchestration_tools

    employee = await employee_engine.get_employee(db, tenant_id, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    response = ai_orchestration_tools.request_benefits_recommendation(tenant_id, str(employee_id), employee.job_title, employee.department)
    if response is None:
        raise HTTPException(status_code=502, detail="Talent Agent unreachable")
    return response
