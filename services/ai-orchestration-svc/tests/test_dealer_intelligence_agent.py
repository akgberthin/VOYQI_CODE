from sqlalchemy import select

from src.models import AIRecommendation, Agent, AgentStatus, RecommendationStatus
from src.core import action_gate
from src.services import dealer_intelligence_agent, dms_tools

TENANT = "voyqi-holdings"


async def test_evaluate_license_renewals_calls_dms_and_logs(db_session, monkeypatch):
    monkeypatch.setattr(dms_tools, "evaluate_license_renewals", lambda tenant_id: [{"id": "rt-1", "renewal_filed": True}])
    result = await dealer_intelligence_agent.evaluate_license_renewals(db_session, TENANT)
    assert result.output == [{"id": "rt-1", "renewal_filed": True}]
    assert result.decision_log.action_taken == "license_renewals_filed"
    assert result.decision_log.outcome == "success"


async def test_evaluate_aging_sweep_calls_dms_and_logs(db_session, monkeypatch):
    monkeypatch.setattr(dms_tools, "evaluate_aging_sweep", lambda tenant_id: [{"id": "v-1", "price_usd": 18000}])
    result = await dealer_intelligence_agent.evaluate_aging_sweep(db_session, TENANT)
    assert result.output[0]["price_usd"] == 18000
    assert result.decision_log.action_taken == "aging_markdowns_applied"


async def _assert_queued_then_executes_on_approval(db_session, result, calls, executor):
    """Tier 2: nothing runs against dms-svc until a human approves the
    queued recommendation; approving it runs exactly that action."""
    assert calls == []
    assert result.output is None
    assert result.decision_log.outcome == "deferred"
    assert (result.decision_log.tier, result.decision_log.approval_required) == (2, True)
    rec = (await db_session.execute(select(AIRecommendation).where(AIRecommendation.id == result.decision_log.recommendation_id))).scalar_one()
    assert rec.status == RecommendationStatus.PENDING
    assert rec.tier == 2 and rec.requires_accountable_role is False
    assert rec.proposed_action["executor"] == executor

    action_gate.execute_approved(TENANT, rec.proposed_action)
    assert len(calls) == 1


async def test_deal_desk_is_tier_2_and_waits_for_approval(db_session, monkeypatch):
    """deal.structure_recommend is tier 2 in tier-policy.yaml — the desk
    no longer approves a deal on its own."""
    calls = []
    monkeypatch.setattr(dms_tools, "desk_and_approve_deal", lambda tenant_id, deal_id: calls.append(deal_id) or {"id": deal_id, "status": "approved"})
    result = await dealer_intelligence_agent.evaluate_deal_desk(db_session, TENANT, "deal-1")
    assert result.decision_log.trigger_context == {"deal_id": "deal-1"}
    await _assert_queued_then_executes_on_approval(db_session, result, calls, "dms.desk_and_approve_deal")
    assert calls == ["deal-1"]


async def test_evaluate_lending_clean_bureau_defers_to_tier_3_approval(db_session, monkeypatch):
    """Credit decisions are tier 3, hard-coded — even a clean bureau
    pull with no OFAC match must not auto-decision. See
    docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §4."""
    calls = []
    monkeypatch.setattr(
        dms_tools, "pull_bureau_and_route",
        lambda tenant_id, app_id, bureau, fico, ofac_match: calls.append(1)
        or {"status": "decisioned", "selected_submission_id": "sub-1"},
    )
    result = await dealer_intelligence_agent.evaluate_lending(db_session, TENANT, "app-1", "experian", 780, False)

    assert calls == []  # pull_bureau_and_route must NOT have been called yet
    assert result.output is None
    assert result.decision_log.action_taken == "credit_decision_pending_human_review"
    assert result.decision_log.outcome == "deferred"
    assert result.decision_log.tier == 3
    assert result.decision_log.approval_required is True
    assert result.decision_log.recommendation_id is not None

    rec_result = await db_session.execute(
        select(AIRecommendation).where(AIRecommendation.id == result.decision_log.recommendation_id)
    )
    rec = rec_result.scalar_one()
    assert rec.status == RecommendationStatus.PENDING
    assert rec.tier == 3
    assert rec.requires_accountable_role is True
    assert rec.proposed_action == {"app_id": "app-1", "bureau": "experian", "fico": 780, "ofac_match": False}


async def test_evaluate_lending_ofac_match_labeled_as_security(db_session, monkeypatch):
    """The DecisionLog reasoning itself must be honest about why
    nothing was routed — a security block, not a business decision."""
    monkeypatch.setattr(dms_tools, "pull_bureau_and_route", lambda tenant_id, app_id, bureau, fico, ofac_match: {"status": "blocked", "selected_submission_id": None})
    result = await dealer_intelligence_agent.evaluate_lending(db_session, TENANT, "app-2", "experian", 780, True)
    assert result.output["status"] == "blocked"
    assert result.decision_log.action_taken == "credit_application_blocked"
    assert "OFAC" in result.decision_log.reasoning
    assert "No override" in result.decision_log.reasoning


async def test_evaluate_service_dispatch_calls_dms_and_logs(db_session, monkeypatch):
    monkeypatch.setattr(dms_tools, "dispatch_repair_order", lambda tenant_id, rooftop_id, vin, customer_name, description, warranty: {"tech": "tech-42", "estimated_duration_days": 3})
    result = await dealer_intelligence_agent.evaluate_service_dispatch(db_session, TENANT, "rt-1", "VIN001", "Bob", "transmission rebuild", False)
    assert result.output["tech"] == "tech-42"
    assert result.decision_log.trigger_context["description"] == "transmission rebuild"


async def test_evaluate_parts_reorder_calls_dms_and_logs(db_session, monkeypatch):
    monkeypatch.setattr(dms_tools, "evaluate_parts_reorder", lambda tenant_id: [{"part_number": "BP-1001", "stock_qty": 22}])
    result = await dealer_intelligence_agent.evaluate_parts_reorder(db_session, TENANT)
    assert result.output[0]["stock_qty"] == 22
    assert result.decision_log.action_taken == "vendor_orders_placed"


async def test_evaluate_loaner_overdue_calls_dms_and_logs(db_session, monkeypatch):
    monkeypatch.setattr(dms_tools, "evaluate_loaner_overdue", lambda tenant_id: [{"id": "loaner-1", "billing_flag": True}])
    result = await dealer_intelligence_agent.evaluate_loaner_overdue(db_session, TENANT)
    assert result.output[0]["billing_flag"] is True


async def test_evaluate_franchise_remediation_calls_dms_and_logs(db_session, monkeypatch):
    monkeypatch.setattr(dms_tools, "evaluate_franchise_remediation", lambda tenant_id: [{"id": "fr-1", "remediation_opened": True}])
    result = await dealer_intelligence_agent.evaluate_franchise_remediation(db_session, TENANT)
    assert result.output[0]["remediation_opened"] is True


async def test_evaluate_dealer_performance_calls_dms_and_logs(db_session, monkeypatch):
    monkeypatch.setattr(dms_tools, "evaluate_dealer_performance", lambda tenant_id: [{"rooftop_id": "rt-3", "verdict": "intervene", "intervention_applied": True}])
    result = await dealer_intelligence_agent.evaluate_dealer_performance(db_session, TENANT)
    assert result.output[0]["verdict"] == "intervene"
    assert result.decision_log.action_taken == "performance_evaluated_and_intervened"


async def test_tier_1_dealer_operations_execute_immediately_and_are_tagged(db_session, monkeypatch):
    monkeypatch.setattr(dms_tools, "evaluate_loaner_overdue", lambda tenant_id: [{"id": "loaner-1"}])
    result = await dealer_intelligence_agent.evaluate_loaner_overdue(db_session, TENANT)
    assert result.output == [{"id": "loaner-1"}]
    assert (result.decision_log.tier, result.decision_log.approval_required) == (1, False)
    assert result.decision_log.recommendation_id is None


async def test_paused_agent_skips_every_capability(db_session, monkeypatch):
    """A single AgentStatus check gates all nine capabilities — none
    of them should call dms-svc at all when paused."""
    agent_row = Agent(tenant_id=TENANT, key=dealer_intelligence_agent.AGENT_KEY, display_name="Dealer Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    calls = []
    monkeypatch.setattr(dms_tools, "evaluate_aging_sweep", lambda tenant_id: calls.append(1) or [])
    result = await dealer_intelligence_agent.evaluate_aging_sweep(db_session, TENANT)
    assert len(calls) == 0
    assert result.skipped_reason == "paused"
    assert result.decision_log.action_taken == "skipped_agent_not_active"


async def test_decision_log_count_increments_on_active_agent(db_session, monkeypatch):
    agent_row = Agent(tenant_id=TENANT, key=dealer_intelligence_agent.AGENT_KEY, display_name="Dealer Intelligence Agent")
    db_session.add(agent_row)
    await db_session.commit()

    monkeypatch.setattr(dms_tools, "evaluate_parts_reorder", lambda tenant_id: [])
    await dealer_intelligence_agent.evaluate_parts_reorder(db_session, TENANT)
    await db_session.refresh(agent_row)
    assert agent_row.decisions_made == 1
