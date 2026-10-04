"""
HRM-002 benefits half. Plan catalog and enrollment status are plain
facts — deterministic CRUD. The one genuine judgment call is which
plan tier best fits an employee, given dependents and existing
coverage — that's the Talent Agent's job, evaluated automatically the
instant request_benefits_recommendation is called (e.g. at hire or
open enrollment), not held until someone remembers to ask.
accept_benefits_recommendation is the sole human-confirmed action that
turns a recommendation into a real BenefitsEnrollment — however
confident the recommendation, enrolling is always a deliberate choice.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    BenefitsEnrollment,
    BenefitsPlan,
    BenefitsPlanType,
    BenefitsRecommendation,
    BenefitsRecommendationStatus,
    BenefitsTier,
    EnrollmentStatus,
)


class RecommendationNotPendingError(ValueError):
    pass


# ── Plan catalog ────────────────────────────────────────────────────────

async def create_plan(db: AsyncSession, tenant_id: str, name: str, plan_type: BenefitsPlanType, tier: BenefitsTier, monthly_premium_usd: float, employer_contribution_pct: float = 0.0) -> BenefitsPlan:
    plan = BenefitsPlan(tenant_id=tenant_id, name=name, plan_type=plan_type, tier=tier, monthly_premium_usd=monthly_premium_usd, employer_contribution_pct=employer_contribution_pct)
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


async def list_plans(db: AsyncSession, tenant_id: str, plan_type: BenefitsPlanType | None = None) -> list[BenefitsPlan]:
    stmt = select(BenefitsPlan).where(BenefitsPlan.tenant_id == tenant_id)
    if plan_type:
        stmt = stmt.where(BenefitsPlan.plan_type == plan_type)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_plan(db: AsyncSession, tenant_id: str, plan_id: uuid.UUID) -> BenefitsPlan | None:
    result = await db.execute(select(BenefitsPlan).where(BenefitsPlan.id == plan_id, BenefitsPlan.tenant_id == tenant_id))
    return result.scalar_one_or_none()


# ── Enrollment ──────────────────────────────────────────────────────────

async def enroll(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, plan_id: uuid.UUID) -> BenefitsEnrollment:
    enrollment = BenefitsEnrollment(tenant_id=tenant_id, employee_id=employee_id, plan_id=plan_id, status=EnrollmentStatus.ACTIVE)
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


async def list_enrollments(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[BenefitsEnrollment]:
    result = await db.execute(select(BenefitsEnrollment).where(BenefitsEnrollment.tenant_id == tenant_id, BenefitsEnrollment.employee_id == employee_id))
    return list(result.scalars().all())


async def terminate_enrollment(db: AsyncSession, tenant_id: str, enrollment_id: uuid.UUID) -> BenefitsEnrollment | None:
    result = await db.execute(select(BenefitsEnrollment).where(BenefitsEnrollment.id == enrollment_id, BenefitsEnrollment.tenant_id == tenant_id))
    enrollment = result.scalar_one_or_none()
    if enrollment is None:
        return None
    enrollment.status = EnrollmentStatus.TERMINATED
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


# ── Recommendations (Talent Agent output, human-confirmed enrollment) ──

async def create_recommendation(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, recommended_plan_id: uuid.UUID, reasoning: str) -> BenefitsRecommendation:
    rec = BenefitsRecommendation(tenant_id=tenant_id, employee_id=employee_id, recommended_plan_id=recommended_plan_id, reasoning=reasoning)
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return rec


async def list_pending_recommendations(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID | None = None) -> list[BenefitsRecommendation]:
    stmt = select(BenefitsRecommendation).where(BenefitsRecommendation.tenant_id == tenant_id, BenefitsRecommendation.status == BenefitsRecommendationStatus.PENDING)
    if employee_id:
        stmt = stmt.where(BenefitsRecommendation.employee_id == employee_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def accept_recommendation(db: AsyncSession, tenant_id: str, recommendation_id: uuid.UUID) -> tuple[BenefitsRecommendation, BenefitsEnrollment]:
    result = await db.execute(select(BenefitsRecommendation).where(BenefitsRecommendation.id == recommendation_id, BenefitsRecommendation.tenant_id == tenant_id))
    rec = result.scalar_one_or_none()
    if rec is None or rec.status != BenefitsRecommendationStatus.PENDING:
        raise RecommendationNotPendingError("recommendation not found or already reviewed")

    enrollment = await enroll(db, tenant_id, rec.employee_id, rec.recommended_plan_id)
    rec.status = BenefitsRecommendationStatus.ACCEPTED
    await db.commit()
    await db.refresh(rec)
    return rec, enrollment


async def reject_recommendation(db: AsyncSession, tenant_id: str, recommendation_id: uuid.UUID) -> BenefitsRecommendation | None:
    result = await db.execute(select(BenefitsRecommendation).where(BenefitsRecommendation.id == recommendation_id, BenefitsRecommendation.tenant_id == tenant_id))
    rec = result.scalar_one_or_none()
    if rec is None or rec.status != BenefitsRecommendationStatus.PENDING:
        return None
    rec.status = BenefitsRecommendationStatus.REJECTED
    await db.commit()
    await db.refresh(rec)
    return rec
