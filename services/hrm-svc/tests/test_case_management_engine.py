import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models import CaseMessageDirection, CaseStatus, CaseType
from src.services import case_management_engine
from src.services.case_management_engine import DraftReplyNotPendingError

TENANT = "voyqi-holdings"


async def test_create_case_sets_real_sla_by_case_type(db_session):
    employee_id = uuid.uuid4()
    case = await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.PAYROLL_INQUIRY, "Missing overtime pay")
    sla_due_at = case.sla_due_at if case.sla_due_at.tzinfo else case.sla_due_at.replace(tzinfo=timezone.utc)
    hours_until_due = (sla_due_at - datetime.now(timezone.utc)).total_seconds() / 3600
    assert 47.9 < hours_until_due <= 48


async def test_complaint_always_escalates_immediately_and_gets_tightest_sla(db_session):
    employee_id = uuid.uuid4()
    case = await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.COMPLAINT, "Workplace concern")
    assert case.status == CaseStatus.ESCALATED
    sla_due_at = case.sla_due_at if case.sla_due_at.tzinfo else case.sla_due_at.replace(tzinfo=timezone.utc)
    hours_until_due = (sla_due_at - datetime.now(timezone.utc)).total_seconds() / 3600
    assert 23.9 < hours_until_due <= 24


async def test_non_complaint_case_starts_open_not_escalated(db_session):
    employee_id = uuid.uuid4()
    case = await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.BENEFITS_QUESTION, "Question about dental coverage")
    assert case.status == CaseStatus.OPEN


async def test_sla_breach_detection(db_session):
    employee_id = uuid.uuid4()
    case = await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.DOCUMENT_REQUEST, "Need W-2 copy")
    assert case_management_engine.is_case_sla_breached(case, as_of=datetime.now(timezone.utc)) is False
    future = datetime.now(timezone.utc) + timedelta(hours=49)
    assert case_management_engine.is_case_sla_breached(case, as_of=future) is True


async def test_resolved_case_never_shows_as_breached(db_session):
    employee_id = uuid.uuid4()
    case = await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.DOCUMENT_REQUEST, "Need W-2 copy")
    await case_management_engine.update_case_status(db_session, TENANT, case.id, CaseStatus.RESOLVED)
    far_future = datetime.now(timezone.utc) + timedelta(days=30)
    resolved_case = await case_management_engine.get_case(db_session, TENANT, case.id)
    assert case_management_engine.is_case_sla_breached(resolved_case, as_of=far_future) is False


async def test_add_message_and_list_in_order(db_session):
    employee_id = uuid.uuid4()
    case = await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.POLICY_QUESTION, "PTO carryover question")
    await case_management_engine.add_message(db_session, TENANT, case.id, CaseMessageDirection.INBOUND, "Can I carry over unused PTO?", sender="employee")
    await case_management_engine.add_message(db_session, TENANT, case.id, CaseMessageDirection.OUTBOUND, "Yes, up to 40 hours per policy.", sender="hr")

    messages = await case_management_engine.list_messages(db_session, TENANT, case.id)
    assert len(messages) == 2
    assert messages[0].direction == CaseMessageDirection.INBOUND


async def test_policy_document_catalog(db_session):
    await case_management_engine.create_policy_document(db_session, TENANT, "PTO Policy", "Employees may carry over up to 40 hours of unused PTO annually.", category="time_off")
    await case_management_engine.create_policy_document(db_session, TENANT, "Remote Work Policy", "Remote work requires manager approval.", category="workplace")

    time_off_docs = await case_management_engine.list_policy_documents(db_session, TENANT, category="time_off")
    assert len(time_off_docs) == 1
    assert "40 hours" in time_off_docs[0].body


async def test_case_draft_reply_hard_flow(db_session):
    employee_id = uuid.uuid4()
    case = await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.POLICY_QUESTION, "PTO carryover question")
    policy = await case_management_engine.create_policy_document(db_session, TENANT, "PTO Policy", "Employees may carry over up to 40 hours annually.", category="time_off")
    draft = await case_management_engine.create_case_draft_reply(db_session, TENANT, case.id, "Per our PTO policy, you may carry over up to 40 hours.", reasoning="grounded in PTO Policy", cites_policy_id=policy.id)

    pending = await case_management_engine.list_pending_case_drafts(db_session, TENANT, case.id)
    assert len(pending) == 1
    assert pending[0].cites_policy_id == policy.id

    approved_draft, message = await case_management_engine.approve_and_send_reply(db_session, TENANT, draft.id)
    assert approved_draft.status.value == "approved_sent"
    assert message.direction == CaseMessageDirection.OUTBOUND
    assert "40 hours" in message.body


async def test_case_draft_reply_can_be_edited_before_sending(db_session):
    employee_id = uuid.uuid4()
    case = await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.POLICY_QUESTION, "Question")
    draft = await case_management_engine.create_case_draft_reply(db_session, TENANT, case.id, "Generic draft")

    _, message = await case_management_engine.approve_and_send_reply(db_session, TENANT, draft.id, final_body="HR-edited final answer")
    assert message.body == "HR-edited final answer"


async def test_reject_case_draft(db_session):
    employee_id = uuid.uuid4()
    case = await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.POLICY_QUESTION, "Question")
    draft = await case_management_engine.create_case_draft_reply(db_session, TENANT, case.id, "x")

    rejected = await case_management_engine.reject_case_draft(db_session, TENANT, draft.id)
    assert rejected.status.value == "rejected"


async def test_cannot_approve_case_draft_twice(db_session):
    employee_id = uuid.uuid4()
    case = await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.POLICY_QUESTION, "Question")
    draft = await case_management_engine.create_case_draft_reply(db_session, TENANT, case.id, "x")
    await case_management_engine.approve_and_send_reply(db_session, TENANT, draft.id)

    with pytest.raises(DraftReplyNotPendingError):
        await case_management_engine.approve_and_send_reply(db_session, TENANT, draft.id)


async def test_list_cases_filters_by_status(db_session):
    employee_id = uuid.uuid4()
    await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.POLICY_QUESTION, "Q1")
    await case_management_engine.create_case(db_session, TENANT, employee_id, CaseType.COMPLAINT, "Q2")

    escalated = await case_management_engine.list_cases(db_session, TENANT, status=CaseStatus.ESCALATED)
    assert len(escalated) == 1
    assert escalated[0].case_type == CaseType.COMPLAINT
