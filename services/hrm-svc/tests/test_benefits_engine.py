import uuid

import pytest

from src.models import BenefitsPlanType, BenefitsTier
from src.services import benefits_engine, employee_engine
from src.services.benefits_engine import RecommendationNotPendingError

TENANT = "voyqi-holdings"


async def _make_plan(db_session, premium=450.0, employer_pct=0.7):
    return await benefits_engine.create_plan(db_session, TENANT, "VOYQI PPO Gold", BenefitsPlanType.MEDICAL, BenefitsTier.EMPLOYEE_ONLY, premium, employer_pct)


async def test_create_and_list_plans(db_session):
    await _make_plan(db_session)
    plans = await benefits_engine.list_plans(db_session, TENANT)
    assert len(plans) == 1


async def test_list_plans_filters_by_type(db_session):
    await _make_plan(db_session)
    await benefits_engine.create_plan(db_session, TENANT, "VOYQI Dental Basic", BenefitsPlanType.DENTAL, BenefitsTier.EMPLOYEE_ONLY, 30.0)

    medical = await benefits_engine.list_plans(db_session, TENANT, plan_type=BenefitsPlanType.MEDICAL)
    assert len(medical) == 1


async def test_enroll_and_terminate(db_session):
    plan = await _make_plan(db_session)
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    enrollment = await benefits_engine.enroll(db_session, TENANT, emp.id, plan.id)
    assert enrollment.status.value == "active"

    terminated = await benefits_engine.terminate_enrollment(db_session, TENANT, enrollment.id)
    assert terminated.status.value == "terminated"


async def test_recommendation_lifecycle_accept(db_session):
    plan = await _make_plan(db_session)
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    rec = await benefits_engine.create_recommendation(db_session, TENANT, emp.id, plan.id, "Best fit for single employee, no dependents on file")

    pending = await benefits_engine.list_pending_recommendations(db_session, TENANT, emp.id)
    assert len(pending) == 1

    accepted_rec, enrollment = await benefits_engine.accept_recommendation(db_session, TENANT, rec.id)
    assert accepted_rec.status.value == "accepted"
    assert enrollment.plan_id == plan.id
    assert enrollment.status.value == "active"


async def test_recommendation_lifecycle_reject(db_session):
    plan = await _make_plan(db_session)
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    rec = await benefits_engine.create_recommendation(db_session, TENANT, emp.id, plan.id, "x")

    rejected = await benefits_engine.reject_recommendation(db_session, TENANT, rec.id)
    assert rejected.status.value == "rejected"

    enrollments = await benefits_engine.list_enrollments(db_session, TENANT, emp.id)
    assert enrollments == []  # rejecting never creates an enrollment


async def test_cannot_accept_recommendation_twice(db_session):
    plan = await _make_plan(db_session)
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    rec = await benefits_engine.create_recommendation(db_session, TENANT, emp.id, plan.id, "x")
    await benefits_engine.accept_recommendation(db_session, TENANT, rec.id)

    with pytest.raises(RecommendationNotPendingError):
        await benefits_engine.accept_recommendation(db_session, TENANT, rec.id)


async def test_accept_unknown_recommendation_raises(db_session):
    with pytest.raises(RecommendationNotPendingError):
        await benefits_engine.accept_recommendation(db_session, TENANT, uuid.uuid4())
