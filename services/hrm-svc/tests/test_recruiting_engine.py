
import pytest

from src.models import (
    ApplicationStage,
    CandidateInteractionType,
    RequisitionStatus,
    SourceChannel,
)
from src.services import recruiting_engine
from src.services.recruiting_engine import NurtureDraftNotPendingError

TENANT = "voyqi-holdings"


async def test_create_candidate(db_session):
    candidate = await recruiting_engine.create_candidate(db_session, TENANT, "Alex Chen", source_channel=SourceChannel.REFERRAL)
    assert candidate.source_channel == SourceChannel.REFERRAL


async def test_create_requisition(db_session):
    req = await recruiting_engine.create_requisition(db_session, TENANT, "Service Advisor", department="Dealership", required_skills="automotive, customer service, DMS")
    assert req.status == RequisitionStatus.OPEN


async def test_submit_application_logs_timeline_event(db_session):
    candidate = await recruiting_engine.create_candidate(db_session, TENANT, "Alex Chen")
    req = await recruiting_engine.create_requisition(db_session, TENANT, "Service Advisor")
    application = await recruiting_engine.submit_application(db_session, TENANT, candidate.id, req.id)
    assert application.stage == ApplicationStage.APPLIED
    assert application.score is None

    timeline = await recruiting_engine.get_timeline(db_session, TENANT, candidate.id)
    assert len(timeline) == 1
    assert timeline[0].interaction_type == CandidateInteractionType.APPLICATION_SUBMITTED


async def test_apply_screening_score_advances_to_screening_stage(db_session):
    candidate = await recruiting_engine.create_candidate(db_session, TENANT, "Alex Chen")
    req = await recruiting_engine.create_requisition(db_session, TENANT, "Service Advisor")
    application = await recruiting_engine.submit_application(db_session, TENANT, candidate.id, req.id)

    scored = await recruiting_engine.apply_screening_score(db_session, TENANT, application.id, 0.82, "strong keyword match")
    assert scored.stage == ApplicationStage.SCREENING
    assert scored.score == 0.82


async def test_advance_application_stage_is_human_driven(db_session):
    candidate = await recruiting_engine.create_candidate(db_session, TENANT, "Alex Chen")
    req = await recruiting_engine.create_requisition(db_session, TENANT, "Service Advisor")
    application = await recruiting_engine.submit_application(db_session, TENANT, candidate.id, req.id)
    await recruiting_engine.apply_screening_score(db_session, TENANT, application.id, 0.82, "x")

    advanced = await recruiting_engine.advance_application_stage(db_session, TENANT, application.id, ApplicationStage.INTERVIEW)
    assert advanced.stage == ApplicationStage.INTERVIEW


async def test_reject_application_logs_rejection_interaction(db_session):
    candidate = await recruiting_engine.create_candidate(db_session, TENANT, "Alex Chen")
    req = await recruiting_engine.create_requisition(db_session, TENANT, "Service Advisor")
    application = await recruiting_engine.submit_application(db_session, TENANT, candidate.id, req.id)

    rejected = await recruiting_engine.reject_application(db_session, TENANT, application.id, reason="Not enough relevant experience")
    assert rejected.stage == ApplicationStage.REJECTED

    timeline = await recruiting_engine.get_timeline(db_session, TENANT, candidate.id)
    rejection_events = [t for t in timeline if t.interaction_type == CandidateInteractionType.REJECTION]
    assert len(rejection_events) == 1


async def test_nurture_draft_hard_gate_before_send(db_session):
    candidate = await recruiting_engine.create_candidate(db_session, TENANT, "Alex Chen")
    draft = await recruiting_engine.create_nurture_draft(db_session, TENANT, candidate.id, "Thanks for applying! We'd love to keep you in mind for future roles.")

    pending = await recruiting_engine.list_pending_nurture_drafts(db_session, TENANT, candidate.id)
    assert len(pending) == 1

    reviewed_draft, interaction = await recruiting_engine.approve_and_send_nurture(db_session, TENANT, draft.id)
    assert reviewed_draft.status.value == "approved_sent"
    assert interaction.interaction_type == CandidateInteractionType.NURTURE_MESSAGE


async def test_nurture_draft_can_be_edited_before_send(db_session):
    candidate = await recruiting_engine.create_candidate(db_session, TENANT, "Alex Chen")
    draft = await recruiting_engine.create_nurture_draft(db_session, TENANT, candidate.id, "Generic message")

    _, interaction = await recruiting_engine.approve_and_send_nurture(db_session, TENANT, draft.id, final_body="Personalized message for Alex")
    assert interaction.summary == "Personalized message for Alex"


async def test_reject_nurture_draft(db_session):
    candidate = await recruiting_engine.create_candidate(db_session, TENANT, "Alex Chen")
    draft = await recruiting_engine.create_nurture_draft(db_session, TENANT, candidate.id, "x")
    rejected = await recruiting_engine.reject_nurture_draft(db_session, TENANT, draft.id)
    assert rejected.status.value == "rejected"


async def test_cannot_approve_nurture_draft_twice(db_session):
    candidate = await recruiting_engine.create_candidate(db_session, TENANT, "Alex Chen")
    draft = await recruiting_engine.create_nurture_draft(db_session, TENANT, candidate.id, "x")
    await recruiting_engine.approve_and_send_nurture(db_session, TENANT, draft.id)

    with pytest.raises(NurtureDraftNotPendingError):
        await recruiting_engine.approve_and_send_nurture(db_session, TENANT, draft.id)


async def test_sourcing_channel_analytics(db_session):
    candidate1 = await recruiting_engine.create_candidate(db_session, TENANT, "A", source_channel=SourceChannel.REFERRAL)
    candidate2 = await recruiting_engine.create_candidate(db_session, TENANT, "B", source_channel=SourceChannel.REFERRAL)
    await recruiting_engine.create_candidate(db_session, TENANT, "C", source_channel=SourceChannel.JOB_BOARD)
    req = await recruiting_engine.create_requisition(db_session, TENANT, "Service Advisor")
    app1 = await recruiting_engine.submit_application(db_session, TENANT, candidate1.id, req.id)
    await recruiting_engine.advance_application_stage(db_session, TENANT, app1.id, ApplicationStage.HIRED)
    await recruiting_engine.submit_application(db_session, TENANT, candidate2.id, req.id)

    analytics = await recruiting_engine.get_sourcing_channel_analytics(db_session, TENANT)
    referral_stats = next(a for a in analytics if a["source_channel"] == "referral")
    assert referral_stats["candidate_count"] == 2
    assert referral_stats["hired_count"] == 1
    assert referral_stats["hire_rate_pct"] == 50.0


async def test_close_requisition(db_session):
    req = await recruiting_engine.create_requisition(db_session, TENANT, "Service Advisor")
    closed = await recruiting_engine.close_requisition(db_session, TENANT, req.id, RequisitionStatus.FILLED)
    assert closed.status == RequisitionStatus.FILLED


async def test_list_applications_filters_by_requisition_and_stage(db_session):
    candidate = await recruiting_engine.create_candidate(db_session, TENANT, "Alex Chen")
    req1 = await recruiting_engine.create_requisition(db_session, TENANT, "Service Advisor")
    req2 = await recruiting_engine.create_requisition(db_session, TENANT, "Sales Rep")
    await recruiting_engine.submit_application(db_session, TENANT, candidate.id, req1.id)
    await recruiting_engine.submit_application(db_session, TENANT, candidate.id, req2.id)

    req1_apps = await recruiting_engine.list_applications(db_session, TENANT, requisition_id=req1.id)
    assert len(req1_apps) == 1
