import uuid

import pytest

from src.models import ApplicationStatus, MobilityClass
from src.services import enrollment_engine
from src.services.enrollment_engine import ApplicationNotApprovedError

TENANT = "voyqi-holdings"


async def test_submit_application(db_session):
    app = await enrollment_engine.submit_application(
        db_session, tenant_id=TENANT, applicant_name="Marcus R.", applicant_email="marcus@example.com",
        requested_class=MobilityClass.EXECUTIVE, market="miami",
    )
    assert app.status == ApplicationStatus.SUBMITTED


async def test_cannot_enroll_before_approval(db_session):
    app = await enrollment_engine.submit_application(
        db_session, TENANT, "Marcus R.", "marcus@example.com", MobilityClass.EXECUTIVE, "miami"
    )
    with pytest.raises(ApplicationNotApprovedError):
        await enrollment_engine.enroll(db_session, TENANT, app.id, "user-100")


async def test_approve_and_enroll_flow(db_session):
    app = await enrollment_engine.submit_application(
        db_session, TENANT, "Marcus R.", "marcus@example.com", MobilityClass.EXECUTIVE, "miami"
    )
    reviewed = await enrollment_engine.review_application(db_session, TENANT, app.id, approve=True, reviewed_by="member-ops-1")
    assert reviewed.status == ApplicationStatus.APPROVED

    profile = await enrollment_engine.enroll(db_session, TENANT, app.id, "user-100")
    assert profile.mobility_class == MobilityClass.EXECUTIVE
    assert profile.full_name == "Marcus R."


async def test_reject_application(db_session):
    app = await enrollment_engine.submit_application(
        db_session, TENANT, "Bad Actor", "bad@example.com", MobilityClass.LUXURY, "miami"
    )
    reviewed = await enrollment_engine.review_application(
        db_session, TENANT, app.id, approve=False, reviewed_by="member-ops-1", rejection_reason="failed KYC"
    )
    assert reviewed.status == ApplicationStatus.REJECTED
    assert reviewed.rejection_reason == "failed KYC"


async def test_enroll_unknown_application_raises(db_session):
    with pytest.raises(ValueError):
        await enrollment_engine.enroll(db_session, TENANT, uuid.uuid4(), "user-200")


async def test_issue_and_bind_card(db_session):
    app = await enrollment_engine.submit_application(
        db_session, TENANT, "Marcus R.", "marcus@example.com", MobilityClass.EXECUTIVE, "miami"
    )
    await enrollment_engine.review_application(db_session, TENANT, app.id, approve=True, reviewed_by="member-ops-1")
    profile = await enrollment_engine.enroll(db_session, TENANT, app.id, "user-100")

    card = await enrollment_engine.issue_card(db_session, TENANT, profile.id, "VQ-9001-EX")
    assert card.is_bound is False

    bound = await enrollment_engine.bind_card(db_session, TENANT, card.id)
    assert bound.is_bound is True


async def test_bind_unknown_card_returns_none(db_session):
    result = await enrollment_engine.bind_card(db_session, TENANT, uuid.uuid4())
    assert result is None
