from unittest.mock import MagicMock

from src.core.anthropic_client import AgentTurnResult, ToolCallResult
from src.models import Agent, AgentStatus
from src.services import marketing_content_agent, marketing_tools

TENANT = "voyqi-holdings"


class _UnconfiguredClient:
    is_configured = False


class _FakeConfiguredClient:
    is_configured = True

    def __init__(self, tool_output):
        self.run_agent_turn = MagicMock(
            return_value=AgentTurnResult(
                final_text="Drafted on-brand copy for this campaign.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="get_campaign_context",
                        tool_input={"campaign_id": "camp-1"},
                        tool_output={"campaign": {"name": "Summer Push"}},
                    ),
                    ToolCallResult(
                        tool_name="create_content_draft",
                        tool_input={"campaign_id": "camp-1", "channel": "email", "body": "text"},
                        tool_output=tool_output,
                    ),
                ],
                stop_reason="end_turn",
            )
        )


async def test_deterministic_fallback_creates_draft(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        marketing_tools,
        "create_content_draft",
        lambda tenant_id, campaign_id, channel, body, subject, agent_key: calls.append(
            (tenant_id, campaign_id, channel, body, subject, agent_key)
        )
        or {"id": "draft-1", "guardrail_findings": []},
    )
    result = await marketing_content_agent.generate_content(
        db_session, TENANT, campaign_id="camp-1", campaign_name="Summer Push",
        channel="email", brief="highlight the new Executive class vehicles",
        client=_UnconfiguredClient(),
    )
    assert result.draft == {"id": "draft-1", "guardrail_findings": []}
    assert "deterministic fallback" in result.decision_log.reasoning
    assert result.decision_log.action_taken == "draft_auto_approved"
    assert len(calls) == 1
    assert calls[0][5] == marketing_content_agent.AGENT_KEY


async def test_fallback_template_never_uses_banned_phrases():
    subject, body = marketing_content_agent._fallback_template(
        "Retention Push", "email", "come back and save"
    )
    banned = ["guaranteed", "risk-free", "no risk", "100% satisfaction", "best in the world", "unbeatable", "can't lose"]
    combined = f"{subject} {body}".lower()
    assert not any(phrase in combined for phrase in banned)


async def test_draft_with_guardrail_findings_marked_deferred(db_session, monkeypatch):
    monkeypatch.setattr(
        marketing_tools,
        "create_content_draft",
        lambda *a, **k: {"id": "draft-2", "guardrail_findings": ["contains prohibited claim: 'guaranteed'"]},
    )
    result = await marketing_content_agent.generate_content(
        db_session, TENANT, campaign_id="camp-2", campaign_name="Test", channel="email",
        brief="anything", client=_UnconfiguredClient(),
    )
    assert result.decision_log.outcome == "deferred"
    assert result.decision_log.action_taken == "draft_held_for_review_by_guardrail"


async def test_paused_agent_generates_no_draft(db_session, monkeypatch):
    agent_row = Agent(
        tenant_id=TENANT, key=marketing_content_agent.AGENT_KEY,
        display_name="Marketing Content Agent", status=AgentStatus.PAUSED,
    )
    db_session.add(agent_row)
    await db_session.commit()

    calls = []
    monkeypatch.setattr(
        marketing_tools, "create_content_draft", lambda *a, **k: calls.append(1) or {}
    )
    result = await marketing_content_agent.generate_content(
        db_session, TENANT, campaign_id="camp-3", campaign_name="Test", channel="email",
        brief="anything", client=_UnconfiguredClient(),
    )
    assert result.draft is None
    assert result.skipped_reason == "paused"
    assert len(calls) == 0  # tool was never called — agent didn't act at all


async def test_live_claude_path_uses_tool_calls(db_session, monkeypatch):
    monkeypatch.setattr(
        marketing_tools, "build_tool_dispatch", lambda tenant_id, agent_key: {
            "get_campaign_context": lambda inp: {}, "create_content_draft": lambda inp: {}
        }
    )
    fake_client = _FakeConfiguredClient(tool_output={"id": "draft-4", "guardrail_findings": []})
    result = await marketing_content_agent.generate_content(
        db_session, TENANT, campaign_id="camp-4", campaign_name="Summer Push", channel="email",
        brief="highlight new inventory", client=fake_client,
    )
    fake_client.run_agent_turn.assert_called_once()
    assert result.draft == {"id": "draft-4", "guardrail_findings": []}
    assert any(tc["tool"] == "get_campaign_context" for tc in result.decision_log.tools_called)
    assert any(tc["tool"] == "create_content_draft" for tc in result.decision_log.tools_called)


async def test_live_claude_path_with_no_draft_tool_call_reports_failure(db_session, monkeypatch):
    class _NoDraftClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(final_text="I looked at the campaign but didn't draft anything.", tool_calls=[])

    monkeypatch.setattr(
        marketing_tools, "build_tool_dispatch", lambda tenant_id, agent_key: {}
    )
    result = await marketing_content_agent.generate_content(
        db_session, TENANT, campaign_id="camp-5", campaign_name="Test", channel="email",
        brief="anything", client=_NoDraftClient(),
    )
    assert result.draft is None
    assert result.decision_log.outcome == "failed"
    assert result.decision_log.action_taken == "no_draft_produced"


async def test_agent_decision_count_increments(db_session, monkeypatch):
    monkeypatch.setattr(
        marketing_tools, "create_content_draft", lambda *a, **k: {"id": "draft-6", "guardrail_findings": []}
    )
    agent_row = Agent(tenant_id=TENANT, key=marketing_content_agent.AGENT_KEY, display_name="Marketing Content Agent")
    db_session.add(agent_row)
    await db_session.commit()

    await marketing_content_agent.generate_content(
        db_session, TENANT, campaign_id="camp-6", campaign_name="Test", channel="email",
        brief="anything", client=_UnconfiguredClient(),
    )
    await db_session.refresh(agent_row)
    assert agent_row.decisions_made == 1
