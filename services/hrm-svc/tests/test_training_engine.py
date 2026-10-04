import uuid

import pytest

from src.models import CourseCategory
from src.services import training_engine
from src.services.training_engine import RecommendationNotPendingError

TENANT = "voyqi-holdings"


async def test_create_and_list_courses(db_session):
    await training_engine.create_course(db_session, TENANT, "Anti-Harassment Training", CourseCategory.COMPLIANCE, is_mandatory=True)
    await training_engine.create_course(db_session, TENANT, "Advanced DMS Operations", CourseCategory.TECHNICAL, department="Dealership")

    all_courses = await training_engine.list_courses(db_session, TENANT)
    assert len(all_courses) == 2


async def test_list_courses_filters_by_department_including_company_wide(db_session):
    await training_engine.create_course(db_session, TENANT, "Anti-Harassment Training", CourseCategory.COMPLIANCE, is_mandatory=True)  # company-wide, department=""
    await training_engine.create_course(db_session, TENANT, "Advanced DMS Operations", CourseCategory.TECHNICAL, department="Dealership")
    await training_engine.create_course(db_session, TENANT, "Hub Safety Protocol", CourseCategory.SAFETY, department="Hub Operations")

    dealership_courses = await training_engine.list_courses(db_session, TENANT, department="Dealership")
    titles = {c.title for c in dealership_courses}
    assert "Advanced DMS Operations" in titles
    assert "Anti-Harassment Training" in titles  # company-wide course included
    assert "Hub Safety Protocol" not in titles


async def test_enroll_and_complete_course(db_session):
    course = await training_engine.create_course(db_session, TENANT, "Safety 101", CourseCategory.SAFETY)
    employee_id = uuid.uuid4()
    completion = await training_engine.enroll_course(db_session, TENANT, employee_id, course.id)
    assert completion.status.value == "in_progress"

    completed = await training_engine.complete_course(db_session, TENANT, completion.id)
    assert completed.status.value == "completed"
    assert completed.completed_at is not None


async def test_get_incomplete_mandatory_courses(db_session):
    employee_id = uuid.uuid4()
    mandatory1 = await training_engine.create_course(db_session, TENANT, "Anti-Harassment Training", CourseCategory.COMPLIANCE, is_mandatory=True)
    mandatory2 = await training_engine.create_course(db_session, TENANT, "Data Security Basics", CourseCategory.COMPLIANCE, is_mandatory=True)
    await training_engine.create_course(db_session, TENANT, "Optional Leadership Workshop", CourseCategory.LEADERSHIP, is_mandatory=False)

    completion = await training_engine.enroll_course(db_session, TENANT, employee_id, mandatory1.id)
    await training_engine.complete_course(db_session, TENANT, completion.id)

    incomplete = await training_engine.get_incomplete_mandatory_courses(db_session, TENANT, employee_id, department="")
    incomplete_ids = {c.id for c in incomplete}
    assert mandatory1.id not in incomplete_ids
    assert mandatory2.id in incomplete_ids


async def test_covenant_acknowledgment(db_session):
    employee_id = uuid.uuid4()
    await training_engine.acknowledge_covenant(db_session, TENANT, employee_id, "v2026.1")

    acks = await training_engine.list_covenant_acknowledgments(db_session, TENANT, employee_id)
    assert len(acks) == 1
    assert acks[0].covenant_version == "v2026.1"


async def test_course_recommendation_accept_flow(db_session):
    course = await training_engine.create_course(db_session, TENANT, "Data Security Basics", CourseCategory.COMPLIANCE, is_mandatory=True)
    employee_id = uuid.uuid4()
    rec = await training_engine.create_course_recommendation(db_session, TENANT, employee_id, course.id, reasoning="mandatory compliance course not yet completed")

    pending = await training_engine.list_pending_recommendations(db_session, TENANT, employee_id)
    assert len(pending) == 1

    accepted_rec, completion = await training_engine.accept_recommendation(db_session, TENANT, rec.id)
    assert accepted_rec.status.value == "accepted"
    assert completion is not None
    assert completion.course_id == course.id


async def test_course_recommendation_reject_flow(db_session):
    employee_id = uuid.uuid4()
    rec = await training_engine.create_course_recommendation(db_session, TENANT, employee_id, None, reasoning="no gaps found")

    rejected = await training_engine.reject_recommendation(db_session, TENANT, rec.id)
    assert rejected.status.value == "rejected"


async def test_cannot_accept_recommendation_twice(db_session):
    course = await training_engine.create_course(db_session, TENANT, "Safety 101", CourseCategory.SAFETY)
    employee_id = uuid.uuid4()
    rec = await training_engine.create_course_recommendation(db_session, TENANT, employee_id, course.id)
    await training_engine.accept_recommendation(db_session, TENANT, rec.id)

    with pytest.raises(RecommendationNotPendingError):
        await training_engine.accept_recommendation(db_session, TENANT, rec.id)


async def test_accept_recommendation_with_no_course_id_creates_no_completion(db_session):
    employee_id = uuid.uuid4()
    rec = await training_engine.create_course_recommendation(db_session, TENANT, employee_id, None, reasoning="no gaps found")
    accepted_rec, completion = await training_engine.accept_recommendation(db_session, TENANT, rec.id)
    assert accepted_rec.status.value == "accepted"
    assert completion is None
