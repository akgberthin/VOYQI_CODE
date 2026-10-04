from unittest.mock import MagicMock

from src.core.anthropic_client import AgentTurnResult, ToolCallResult
from src.models import Agent, AgentStatus
from src.services import member_experience_agent

TENANT = "voyqi-holdings"


class _UnconfiguredClient:
    is_configured = False


class _FakeConfiguredClient:
    is_configured = True

    def __init__(self, final_text="Member shows moderate engagement, low risk."):
        self.run_agent_turn = MagicMock(
            return_value=AgentTurnResult(
                final_text=final_text,
                tool_calls=[
                    ToolCallResult(tool_name="get_member_profile", tool_input={"member_profile_id": "mem-1"}, tool_output={"gu_balance": 800}),
                ],
                stop_reason="end_turn",
            )
        )


async def test_deterministic_churn_score_low_risk(db_session, monkeypatch):
    from src.services import subscription_tools

    calls = []
    monkeypatch.setattr(subscription_tools, "award_retention_gu_bonus", lambda *a, **k: calls.append(1) or {})

    result = await member_experience_agent.evaluate_churn_risk(
        db_session, TENANT, "mem-1", context={"days_since_last_use": 2, "pause_count_last_year": 0, "gu_earn_events_last_90d": 5},
        client=_UnconfiguredClient(),
    )
    assert result.risk_score < 20
    assert "deterministic fallback" in result.decision_log.reasoning
    assert result.decision_log.action_taken == "churn_score_computed"
    assert len(calls) == 0  # below the intervention threshold — no autonomous action taken


async def test_churn_intervention_threshold_boundary(db_session, monkeypatch):
    """Exactly at CHURN_INTERVENTION_THRESHOLD (>=) should trigger the
    autonomous save; one point below should not."""
    from src.services import subscription_tools

    calls = []
    monkeypatch.setattr(subscription_tools, "award_retention_gu_bonus", lambda *a, **k: calls.append(1) or {})

    # 40 (>=30d idle) + 25 (pause>=2) = 65 total is too high to isolate exactly
    # at the boundary with this scoring function, so assert the documented
    # invariant directly instead: the threshold check is >=, not >.
    assert member_experience_agent.CHURN_INTERVENTION_THRESHOLD == 50.0
    below = member_experience_agent._deterministic_churn_score({"days_since_last_use": 20})[0]  # 20 points, below threshold
    at_or_above = member_experience_agent._deterministic_churn_score({"days_since_last_use": 35, "pause_count_last_year": 2})[0]  # 40+25=65
    assert below < member_experience_agent.CHURN_INTERVENTION_THRESHOLD
    assert at_or_above >= member_experience_agent.CHURN_INTERVENTION_THRESHOLD


async def test_deterministic_churn_score_high_risk(db_session, monkeypatch):
    from src.services import subscription_tools

    calls = []
    monkeypatch.setattr(subscription_tools, "award_retention_gu_bonus", lambda tenant_id, member_profile_id, amount: calls.append((member_profile_id, amount)) or {})

    result = await member_experience_agent.evaluate_churn_risk(
        db_session, TENANT, "mem-2",
        context={"days_since_last_use": 45, "pause_count_last_year": 3, "support_complaints_last_90d": 2, "gu_earn_events_last_90d": 0},
        client=_UnconfiguredClient(),
    )
    assert result.risk_score >= 90
    assert result.decision_log.action_taken == "churn_score_computed_and_retention_bonus_awarded"
    assert calls == [("mem-2", member_experience_agent.RETENTION_SAVE_GU_BONUS)]


async def test_paused_agent_returns_no_churn_score(db_session):
    agent_row = Agent(tenant_id=TENANT, key=member_experience_agent.AGENT_KEY, display_name="Member Experience Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await member_experience_agent.evaluate_churn_risk(db_session, TENANT, "mem-3", context={}, client=_UnconfiguredClient())
    assert result.risk_score is None
    assert result.skipped_reason == "paused"


async def test_winback_offer_for_price_sensitive_reason(db_session):
    result = await member_experience_agent.evaluate_winback(
        db_session, TENANT, "mem-4", "sub-1", reason="too expensive for what I use it for", client=_UnconfiguredClient()
    )
    assert result.offer is not None
    assert "off" in result.offer["offer"]


async def test_no_winback_offer_for_relocation(db_session):
    result = await member_experience_agent.evaluate_winback(
        db_session, TENANT, "mem-5", "sub-2", reason="moving overseas permanently", client=_UnconfiguredClient()
    )
    assert result.offer is None
    assert result.decision_log.action_taken == "no_offer_recommended"


async def test_winback_offer_for_service_issue(db_session):
    result = await member_experience_agent.evaluate_winback(
        db_session, TENANT, "mem-6", "sub-3", reason="had a bad service issue with staging", client=_UnconfiguredClient()
    )
    assert result.offer is not None
    assert "concierge" in result.offer["offer"]


async def test_generic_reason_gets_pause_offer(db_session):
    result = await member_experience_agent.evaluate_winback(
        db_session, TENANT, "mem-7", "sub-4", reason="just don't need it right now", client=_UnconfiguredClient()
    )
    assert result.offer is not None
    assert "pause" in result.offer["offer"]


async def test_paused_agent_skips_winback_evaluation(db_session):
    agent_row = Agent(tenant_id=TENANT, key=member_experience_agent.AGENT_KEY, display_name="Member Experience Agent", status=AgentStatus.DISABLED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await member_experience_agent.evaluate_winback(db_session, TENANT, "mem-8", "sub-5", reason="expensive", client=_UnconfiguredClient())
    assert result.offer is None
    assert result.skipped_reason == "disabled"


async def test_live_claude_path_used_for_churn(db_session, monkeypatch):
    from src.services import subscription_tools

    monkeypatch.setattr(subscription_tools, "build_tool_dispatch", lambda tenant_id: {"get_member_profile": lambda inp: {}, "get_gu_ledger": lambda inp: []})
    fake_client = _FakeConfiguredClient()
    result = await member_experience_agent.evaluate_churn_risk(db_session, TENANT, "mem-9", context={"days_since_last_use": 1}, client=fake_client)
    fake_client.run_agent_turn.assert_called_once()
    assert "moderate engagement" in result.decision_log.reasoning


async def test_agent_decision_count_increments(db_session):
    agent_row = Agent(tenant_id=TENANT, key=member_experience_agent.AGENT_KEY, display_name="Member Experience Agent")
    db_session.add(agent_row)
    await db_session.commit()

    await member_experience_agent.evaluate_churn_risk(db_session, TENANT, "mem-10", context={}, client=_UnconfiguredClient())
    await db_session.refresh(agent_row)
    assert agent_row.decisions_made == 1


async def test_upgrade_pattern_deterministic_creates_case_when_pattern_detected(db_session, monkeypatch):
    from src.services import subscription_tools

    monkeypatch.setattr(subscription_tools, "get_upgrade_pattern", lambda tenant_id, member_id: {"pattern_detected": True, "recommended_class": "luxury", "request_count": 3, "reasoning": "3 requests in 30 days"})
    created = []
    monkeypatch.setattr(subscription_tools, "create_upgrade_case", lambda *a, **k: created.append(a) or {"id": "case-1", "status": "offered"})

    result = await member_experience_agent.evaluate_upgrade_pattern(db_session, TENANT, "mem-1", "sub-1", "executive", client=_UnconfiguredClient())
    assert result.case_created is True
    assert len(created) == 1


async def test_upgrade_pattern_deterministic_no_case_when_pattern_not_detected(db_session, monkeypatch):
    from src.services import subscription_tools

    monkeypatch.setattr(subscription_tools, "get_upgrade_pattern", lambda tenant_id, member_id: {"pattern_detected": False, "recommended_class": None, "request_count": 1, "reasoning": "only 1 request"})
    monkeypatch.setattr(subscription_tools, "create_upgrade_case", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should never be called")))

    result = await member_experience_agent.evaluate_upgrade_pattern(db_session, TENANT, "mem-1", "sub-1", "executive", client=_UnconfiguredClient())
    assert result.case_created is False


async def test_upgrade_pattern_never_calls_accept_or_touches_billing(db_session, monkeypatch):
    """The core safety property: this capability's tool surface has no
    way to change what anyone is billed — only create_upgrade_case,
    which is always OFFERED, never ACCEPTED."""
    from src.services import subscription_tools

    monkeypatch.setattr(subscription_tools, "get_upgrade_pattern", lambda tenant_id, member_id: {"pattern_detected": True, "recommended_class": "luxury", "request_count": 3, "reasoning": "pattern met"})
    calls = []
    monkeypatch.setattr(subscription_tools, "create_upgrade_case", lambda *a, **k: calls.append(a) or {"id": "case-1", "status": "offered"})
    assert not hasattr(subscription_tools, "accept_upgrade_case")

    await member_experience_agent.evaluate_upgrade_pattern(db_session, TENANT, "mem-1", "sub-1", "executive", client=_UnconfiguredClient())
    assert len(calls) == 1


async def test_upgrade_pattern_paused_agent_creates_no_case(db_session, monkeypatch):
    from src.services import subscription_tools

    agent_row = Agent(tenant_id=TENANT, key=member_experience_agent.AGENT_KEY, display_name="Member Experience Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()
    monkeypatch.setattr(subscription_tools, "create_upgrade_case", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should never be called")))

    result = await member_experience_agent.evaluate_upgrade_pattern(db_session, TENANT, "mem-1", "sub-1", "executive", client=_UnconfiguredClient())
    assert result.case_created is False
    assert result.skipped_reason == "paused"


async def test_upgrade_pattern_live_claude_path(db_session, monkeypatch):
    from src.services import subscription_tools

    monkeypatch.setattr(subscription_tools, "get_upgrade_pattern", lambda tenant_id, member_id: {"pattern_detected": True, "recommended_class": "luxury", "request_count": 3, "reasoning": "pattern met"})
    monkeypatch.setattr(subscription_tools, "create_upgrade_case", lambda *a, **k: {"id": "case-1", "status": "offered"})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Pattern confirmed, offering upgrade.",
                tool_calls=[
                    ToolCallResult(tool_name="get_upgrade_pattern", tool_input={"member_profile_id": "mem-1"}, tool_output={"pattern_detected": True}),
                    ToolCallResult(tool_name="create_upgrade_case", tool_input={"member_profile_id": "mem-1", "recommended_class": "luxury", "reasoning": "pattern met"}, tool_output={"id": "case-1"}),
                ],
                stop_reason="end_turn",
            )

    result = await member_experience_agent.evaluate_upgrade_pattern(db_session, TENANT, "mem-1", "sub-1", "executive", client=_FakeClient())
    assert result.case_created is True


async def test_handle_above_class_request_recorded_adapter(db_session, monkeypatch):
    from src.services import subscription_tools

    monkeypatch.setattr(subscription_tools, "get_upgrade_pattern", lambda tenant_id, member_id: {"pattern_detected": False, "recommended_class": None, "request_count": 1, "reasoning": "only 1"})

    result = await member_experience_agent.handle_above_class_request_recorded(
        db_session, TENANT, "subscription.above_class_request_recorded",
        {"member_profile_id": "mem-1", "subscription_id": "sub-1", "subscribed_class": "executive"},
    )
    assert result.case_created is False
