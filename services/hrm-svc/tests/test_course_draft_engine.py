import uuid

import pytest

from src.models import CourseCategory, CourseContentType
from src.services import training_engine
from src.services.training_engine import CourseDraftNotPendingError

TENANT = "voyqi-holdings"


async def test_create_course_draft_starts_pending(db_session):
    draft = await training_engine.create_course_draft(db_session, TENANT, "defensive driving safety", "Defensive Driving Safety", CourseCategory.SAFETY)
    assert draft.status.value == "pending_review"


async def test_publish_course_draft_creates_a_real_catalog_course(db_session):
    draft = await training_engine.create_course_draft(
        db_session, TENANT, "defensive driving safety", "Defensive Driving Safety", CourseCategory.SAFETY,
        draft_description="Covers hazard recognition and braking technique.", draft_content_type=CourseContentType.VIDEO,
    )
    published_draft, course = await training_engine.publish_course_draft(db_session, TENANT, draft.id, is_mandatory=True, department="Dealership")
    assert published_draft.status.value == "published"
    assert course.title == "Defensive Driving Safety"
    assert course.content_type.value == "video"
    assert course.is_mandatory is True


async def test_publish_course_draft_can_override_title_and_description(db_session):
    draft = await training_engine.create_course_draft(db_session, TENANT, "prompt", "Draft Title", CourseCategory.TECHNICAL, draft_description="Draft description")
    _, course = await training_engine.publish_course_draft(db_session, TENANT, draft.id, final_title="Final Title", final_description="Final description")
    assert course.title == "Final Title"
    assert course.description == "Final description"


async def test_cannot_publish_course_draft_twice(db_session):
    draft = await training_engine.create_course_draft(db_session, TENANT, "prompt", "Title", CourseCategory.TECHNICAL)
    await training_engine.publish_course_draft(db_session, TENANT, draft.id)

    with pytest.raises(CourseDraftNotPendingError):
        await training_engine.publish_course_draft(db_session, TENANT, draft.id)


async def test_reject_course_draft(db_session):
    draft = await training_engine.create_course_draft(db_session, TENANT, "prompt", "Title", CourseCategory.TECHNICAL)
    rejected = await training_engine.reject_course_draft(db_session, TENANT, draft.id)
    assert rejected.status.value == "rejected"


async def test_rejected_draft_creates_no_course(db_session):
    draft = await training_engine.create_course_draft(db_session, TENANT, "prompt", "Title", CourseCategory.TECHNICAL)
    await training_engine.reject_course_draft(db_session, TENANT, draft.id)

    courses = await training_engine.list_courses(db_session, TENANT)
    assert len(courses) == 0


async def test_list_pending_course_drafts_excludes_reviewed(db_session):
    draft1 = await training_engine.create_course_draft(db_session, TENANT, "p1", "Title 1", CourseCategory.TECHNICAL)
    await training_engine.create_course_draft(db_session, TENANT, "p2", "Title 2", CourseCategory.SAFETY)
    await training_engine.publish_course_draft(db_session, TENANT, draft1.id)

    pending = await training_engine.list_pending_course_drafts(db_session, TENANT)
    assert len(pending) == 1
    assert pending[0].draft_title == "Title 2"


async def test_publish_unknown_draft_raises(db_session):
    with pytest.raises(CourseDraftNotPendingError):
        await training_engine.publish_course_draft(db_session, TENANT, uuid.uuid4())


async def test_create_course_supports_video_content_type_and_url(db_session):
    course = await training_engine.create_course(
        db_session, TENANT, "Defensive Driving", CourseCategory.SAFETY, content_type=CourseContentType.VIDEO, content_url="https://academy.voyqi.internal/videos/defensive-driving", description="Video course on defensive driving."
    )
    assert course.content_type.value == "video"
    assert "defensive-driving" in course.content_url
