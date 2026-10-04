import uuid
from datetime import datetime, timedelta, timezone

from src.services import billing_engine

TENANT = "voyqi-holdings"


def _period():
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=30)
    return start, end


async def test_create_invoice(db_session):
    start, end = _period()
    invoice = await billing_engine.create_invoice(
        db_session, tenant_id=TENANT, member_profile_id=uuid.uuid4(), subscription_id=uuid.uuid4(),
        amount_usd=450.0, billing_period_start=start, billing_period_end=end,
    )
    assert invoice.status.value == "open"


async def test_mark_paid(db_session):
    start, end = _period()
    invoice = await billing_engine.create_invoice(
        db_session, TENANT, uuid.uuid4(), uuid.uuid4(), 450.0, start, end,
    )
    paid = await billing_engine.mark_paid(db_session, TENANT, invoice.id)
    assert paid.status.value == "paid"
    assert paid.paid_at is not None


async def test_dunning_marks_past_due_after_max_retries(db_session):
    start, end = _period()
    invoice = await billing_engine.create_invoice(
        db_session, TENANT, uuid.uuid4(), uuid.uuid4(), 450.0, start, end,
    )
    for _ in range(billing_engine.MAX_DUNNING_RETRIES):
        updated = await billing_engine.record_payment_failure(db_session, TENANT, invoice.id)
        assert updated.status.value == "open"  # still retrying

    final = await billing_engine.record_payment_failure(db_session, TENANT, invoice.id)
    assert final.status.value == "past_due"


async def test_issue_refund(db_session):
    start, end = _period()
    invoice = await billing_engine.create_invoice(
        db_session, TENANT, uuid.uuid4(), uuid.uuid4(), 450.0, start, end,
    )
    refund = await billing_engine.issue_refund(db_session, TENANT, invoice.id, 450.0, "service issue", "billing-ops-1")
    assert refund.amount_usd == 450.0

    invoices = await billing_engine.list_invoices_for_member(db_session, TENANT, invoice.member_profile_id)
    assert invoices[0].status.value == "refunded"


async def test_net_amount_due_applies_proration(db_session):
    start, end = _period()
    invoice = await billing_engine.create_invoice(
        db_session, TENANT, uuid.uuid4(), uuid.uuid4(), 450.0, start, end, proration_credit_usd=100.0,
    )
    net = await billing_engine.net_amount_due(invoice)
    assert net == 350.0


async def test_mark_paid_unknown_invoice_returns_none(db_session):
    result = await billing_engine.mark_paid(db_session, TENANT, uuid.uuid4())
    assert result is None
