import uuid

import pytest

from src.models import EmailRecipientScope
from src.services import email_engine, employee_engine
from src.services.email_engine import EmailNotDraftError

TENANT = "voyqi-holdings"


async def test_create_email_draft_starts_in_draft_status(db_session):
    email = await email_engine.create_email_draft(db_session, TENANT, EmailRecipientScope.ALL_STAFF, "Q3 Update", "Body text.")
    assert email.status.value == "draft"
    assert email.sent_at is None


async def test_manual_email_is_not_ai_generated_by_default(db_session):
    email = await email_engine.create_email_draft(db_session, TENANT, EmailRecipientScope.ALL_STAFF, "Subject", "Body")
    assert email.is_ai_generated is False


async def test_send_email_marks_sent_and_sets_timestamp(db_session):
    email = await email_engine.create_email_draft(db_session, TENANT, EmailRecipientScope.ALL_STAFF, "Subject", "Body")
    sent = await email_engine.send_email(db_session, TENANT, email.id)
    assert sent.status.value == "sent"
    assert sent.sent_at is not None


async def test_cannot_send_email_twice(db_session):
    email = await email_engine.create_email_draft(db_session, TENANT, EmailRecipientScope.ALL_STAFF, "Subject", "Body")
    await email_engine.send_email(db_session, TENANT, email.id)

    with pytest.raises(EmailNotDraftError):
        await email_engine.send_email(db_session, TENANT, email.id)


async def test_send_unknown_email_raises(db_session):
    with pytest.raises(EmailNotDraftError):
        await email_engine.send_email(db_session, TENANT, uuid.uuid4())


async def test_resolve_recipient_count_single_employee_is_always_one(db_session):
    count = await email_engine.resolve_recipient_count(db_session, TENANT, EmailRecipientScope.SINGLE_EMPLOYEE)
    assert count == 1


async def test_resolve_recipient_count_all_staff_matches_real_active_headcount(db_session):
    await employee_engine.create_employee(db_session, TENANT, "A")
    await employee_engine.create_employee(db_session, TENANT, "B")
    terminated = await employee_engine.create_employee(db_session, TENANT, "C")
    await employee_engine.initiate_offboarding(db_session, TENANT, terminated.id)

    count = await email_engine.resolve_recipient_count(db_session, TENANT, EmailRecipientScope.ALL_STAFF)
    assert count == 2  # terminated employee excluded


async def test_list_email_drafts_filters_by_status(db_session):
    email1 = await email_engine.create_email_draft(db_session, TENANT, EmailRecipientScope.ALL_STAFF, "Sent one", "Body")
    await email_engine.create_email_draft(db_session, TENANT, EmailRecipientScope.ALL_STAFF, "Still draft", "Body")
    await email_engine.send_email(db_session, TENANT, email1.id)

    drafts = await email_engine.list_email_drafts(db_session, TENANT, status=None)
    assert len(drafts) == 2
    only_sent = [e for e in drafts if e.status.value == "sent"]
    assert len(only_sent) == 1


async def test_single_employee_email_records_the_target(db_session):
    employee = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    email = await email_engine.create_email_draft(db_session, TENANT, EmailRecipientScope.SINGLE_EMPLOYEE, "Subject", "Body", recipient_employee_id=employee.id)
    assert email.recipient_employee_id == employee.id
