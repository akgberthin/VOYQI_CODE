import uuid

import pytest

from src.services import crm_engine

TENANT = "voyqi-holdings"


async def _capture_lead(db_session, email="lead@voyqi.com"):
    return await crm_engine.capture_lead(db_session, tenant_id=TENANT, first_name="Jordan", last_name="Rivera", email=email, source="web")


@pytest.mark.asyncio
async def test_capture_lead_starts_as_new(db_session):
    lead = await _capture_lead(db_session)
    await db_session.commit()
    assert lead.status == "new"


@pytest.mark.asyncio
async def test_qualify_lead_true_sets_qualified(db_session):
    lead = await _capture_lead(db_session)
    await db_session.commit()
    lead_id = lead.id

    qualified = await crm_engine.qualify_lead(db_session, tenant_id=TENANT, lead_id=lead_id, qualified=True)
    await db_session.commit()
    assert qualified.status == "qualified"


@pytest.mark.asyncio
async def test_qualify_lead_false_sets_disqualified_with_reason(db_session):
    lead = await _capture_lead(db_session)
    await db_session.commit()
    lead_id = lead.id

    disqualified = await crm_engine.qualify_lead(db_session, tenant_id=TENANT, lead_id=lead_id, qualified=False, reason="Budget mismatch")
    await db_session.commit()
    assert disqualified.status == "disqualified"
    assert disqualified.disqualify_reason == "Budget mismatch"


@pytest.mark.asyncio
async def test_convert_lead_to_deal_creates_deal_and_marks_lead_converted(db_session):
    lead = await _capture_lead(db_session)
    await db_session.commit()
    lead_id = lead.id
    owner_id = uuid.uuid4()

    deal = await crm_engine.convert_lead_to_deal(db_session, tenant_id=TENANT, lead_id=lead_id, deal_name="Rivera — Executive plan", value_cents=85900, owner_id=owner_id, mobility_class="executive")
    await db_session.commit()
    assert deal.stage == "prospecting"
    assert deal.lead_id == lead_id

    refreshed_lead = await crm_engine.get_lead(db_session, tenant_id=TENANT, lead_id=lead_id)
    assert refreshed_lead.status == "converted"


@pytest.mark.asyncio
async def test_cannot_convert_an_already_converted_lead(db_session):
    lead = await _capture_lead(db_session)
    await db_session.commit()
    lead_id = lead.id
    owner_id = uuid.uuid4()

    await crm_engine.convert_lead_to_deal(db_session, tenant_id=TENANT, lead_id=lead_id, deal_name="First deal", value_cents=50000, owner_id=owner_id)
    await db_session.commit()

    with pytest.raises(crm_engine.LeadAlreadyConvertedError):
        await crm_engine.convert_lead_to_deal(db_session, tenant_id=TENANT, lead_id=lead_id, deal_name="Second deal", value_cents=60000, owner_id=owner_id)


@pytest.mark.asyncio
async def test_cannot_qualify_an_already_converted_lead(db_session):
    lead = await _capture_lead(db_session)
    await db_session.commit()
    lead_id = lead.id
    await crm_engine.convert_lead_to_deal(db_session, tenant_id=TENANT, lead_id=lead_id, deal_name="Deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()

    with pytest.raises(crm_engine.LeadAlreadyConvertedError):
        await crm_engine.qualify_lead(db_session, tenant_id=TENANT, lead_id=lead_id, qualified=True)


@pytest.mark.asyncio
async def test_create_deal_directly_without_a_lead(db_session):
    deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Corporate fleet inquiry", value_cents=500000, owner_id=uuid.uuid4(), mobility_class="performance")
    await db_session.commit()
    assert deal.lead_id is None
    assert deal.stage == "prospecting"


@pytest.mark.asyncio
async def test_advance_deal_through_open_stages(db_session):
    deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()
    deal_id = deal.id

    for stage in ["qualification", "proposal", "negotiation"]:
        updated = await crm_engine.advance_deal_stage(db_session, tenant_id=TENANT, deal_id=deal_id, new_stage=stage)
        await db_session.commit()
        assert updated.stage == stage


@pytest.mark.asyncio
async def test_advancing_to_a_closed_stage_sets_closed_at(db_session):
    deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()
    deal_id = deal.id

    closed = await crm_engine.advance_deal_stage(db_session, tenant_id=TENANT, deal_id=deal_id, new_stage="closed_won")
    await db_session.commit()
    assert closed.closed_at is not None


@pytest.mark.asyncio
async def test_cannot_advance_an_already_closed_deal(db_session):
    deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()
    deal_id = deal.id
    await crm_engine.close_deal(db_session, tenant_id=TENANT, deal_id=deal_id, won=True)
    await db_session.commit()

    with pytest.raises(crm_engine.DealClosedError):
        await crm_engine.advance_deal_stage(db_session, tenant_id=TENANT, deal_id=deal_id, new_stage="proposal")


@pytest.mark.asyncio
async def test_advance_to_unrecognized_stage_raises(db_session):
    deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()
    deal_id = deal.id

    with pytest.raises(crm_engine.InvalidStageTransitionError):
        await crm_engine.advance_deal_stage(db_session, tenant_id=TENANT, deal_id=deal_id, new_stage="not_a_real_stage")


@pytest.mark.asyncio
async def test_close_deal_won_and_lost(db_session):
    won_deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Won deal", value_cents=50000, owner_id=uuid.uuid4())
    lost_deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Lost deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()
    won_id, lost_id = won_deal.id, lost_deal.id

    won = await crm_engine.close_deal(db_session, tenant_id=TENANT, deal_id=won_id, won=True)
    lost = await crm_engine.close_deal(db_session, tenant_id=TENANT, deal_id=lost_id, won=False, reason="Chose a competitor")
    await db_session.commit()

    assert won.stage == "closed_won"
    assert lost.stage == "closed_lost"
    assert lost.close_reason == "Chose a competitor"


@pytest.mark.asyncio
async def test_cannot_close_an_already_closed_deal(db_session):
    deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()
    deal_id = deal.id
    await crm_engine.close_deal(db_session, tenant_id=TENANT, deal_id=deal_id, won=True)
    await db_session.commit()

    with pytest.raises(crm_engine.DealClosedError):
        await crm_engine.close_deal(db_session, tenant_id=TENANT, deal_id=deal_id, won=False)


@pytest.mark.asyncio
async def test_reopen_closed_deal_resets_close_fields(db_session):
    deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()
    deal_id = deal.id
    await crm_engine.close_deal(db_session, tenant_id=TENANT, deal_id=deal_id, won=False, reason="Timing")
    await db_session.commit()

    reopened = await crm_engine.reopen_deal(db_session, tenant_id=TENANT, deal_id=deal_id, new_stage="negotiation")
    await db_session.commit()
    assert reopened.stage == "negotiation"
    assert reopened.closed_at is None
    assert reopened.close_reason is None


@pytest.mark.asyncio
async def test_cannot_reopen_an_open_deal(db_session):
    deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()
    deal_id = deal.id

    with pytest.raises(crm_engine.InvalidStageTransitionError):
        await crm_engine.reopen_deal(db_session, tenant_id=TENANT, deal_id=deal_id)


@pytest.mark.asyncio
async def test_cannot_reopen_into_a_closed_stage(db_session):
    deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()
    deal_id = deal.id
    await crm_engine.close_deal(db_session, tenant_id=TENANT, deal_id=deal_id, won=True)
    await db_session.commit()

    with pytest.raises(crm_engine.InvalidStageTransitionError):
        await crm_engine.reopen_deal(db_session, tenant_id=TENANT, deal_id=deal_id, new_stage="closed_lost")


@pytest.mark.asyncio
async def test_log_activity_requires_a_deal_or_lead(db_session):
    with pytest.raises(ValueError):
        await crm_engine.log_activity(db_session, tenant_id=TENANT, activity_type="call", subject="Follow-up", created_by=uuid.uuid4())


@pytest.mark.asyncio
async def test_log_activity_on_a_deal(db_session):
    deal = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="Deal", value_cents=50000, owner_id=uuid.uuid4())
    await db_session.commit()
    deal_id = deal.id

    activity = await crm_engine.log_activity(db_session, tenant_id=TENANT, deal_id=deal_id, activity_type="call", subject="Discovery call", created_by=uuid.uuid4(), notes="Went well")
    await db_session.commit()
    assert activity.subject == "Discovery call"


@pytest.mark.asyncio
async def test_pipeline_summary_aggregates_correctly(db_session):
    owner = uuid.uuid4()
    d1 = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="D1", value_cents=10000, owner_id=owner)
    d2 = await crm_engine.create_deal(db_session, tenant_id=TENANT, name="D2", value_cents=20000, owner_id=owner)
    await crm_engine.create_deal(db_session, tenant_id=TENANT, name="D3", value_cents=30000, owner_id=owner)  # stays in prospecting — the control case
    await db_session.commit()
    d1_id, d2_id = d1.id, d2.id

    await crm_engine.advance_deal_stage(db_session, tenant_id=TENANT, deal_id=d1_id, new_stage="qualification")
    await crm_engine.advance_deal_stage(db_session, tenant_id=TENANT, deal_id=d2_id, new_stage="qualification")
    await db_session.commit()

    summary = await crm_engine.get_pipeline_summary(db_session, tenant_id=TENANT, owner_id=owner)
    assert summary["qualification"]["count"] == 2
    assert summary["qualification"]["total_value_cents"] == 30000
    assert summary["prospecting"]["count"] == 1
    assert summary["prospecting"]["total_value_cents"] == 30000
    assert summary["closed_won"]["count"] == 0
