
from src.core.anthropic_client import AgentTurnResult, ToolCallResult
from src.models import Agent, AgentStatus
from src.services import concierge_agent, crm_tools

TENANT = "voyqi-holdings"


class _UnconfiguredClient:
    is_configured = False


def _patch_crm_writes(monkeypatch, draft_id="draft-1"):
    monkeypatch.setattr(crm_tools, "set_message_sentiment_intent", lambda *a, **k: {})
    monkeypatch.setattr(crm_tools, "create_draft_reply", lambda *a, **k: {"id": draft_id})
    monkeypatch.setattr(crm_tools, "escalate_conversation", lambda *a, **k: {})


async def test_negative_complaint_escalates_and_drafts_apology(db_session, monkeypatch):
    _patch_crm_writes(monkeypatch)
    escalate_calls = []
    monkeypatch.setattr(crm_tools, "escalate_conversation", lambda *a, **k: escalate_calls.append(a) or {})

    result = await concierge_agent.evaluate_message(
        db_session, TENANT, "msg-1", "conv-1", "This is unacceptable, my reservation was cancelled and nobody told me, I want a refund",
        client=_UnconfiguredClient(),
    )
    assert result.sentiment == "negative"
    assert result.intent == "complaint"
    assert result.escalated is True
    assert len(escalate_calls) == 1


async def test_sales_inquiry_no_escalation(db_session, monkeypatch):
    _patch_crm_writes(monkeypatch)

    result = await concierge_agent.evaluate_message(
        db_session, TENANT, "msg-2", "conv-2", "Hi, what's the price of the Executive tier? I'm interested in upgrading.",
        client=_UnconfiguredClient(),
    )
    assert result.intent == "sales_inquiry"
    assert result.escalated is False
    assert result.draft_reply_id == "draft-1"


async def test_support_request_neutral_sentiment(db_session, monkeypatch):
    _patch_crm_writes(monkeypatch)

    result = await concierge_agent.evaluate_message(
        db_session, TENANT, "msg-3", "conv-3", "I'm stuck setting up my account, could you help me figure it out?",
        client=_UnconfiguredClient(),
    )
    assert result.intent == "support_request"
    assert result.sentiment == "neutral"


async def test_positive_general_message(db_session, monkeypatch):
    _patch_crm_writes(monkeypatch)

    result = await concierge_agent.evaluate_message(
        db_session, TENANT, "msg-4", "conv-4", "Thanks so much, this is awesome!",
        client=_UnconfiguredClient(),
    )
    assert result.sentiment == "positive"
    assert result.intent == "general"
    assert result.escalated is False


async def test_negative_without_complaint_keywords_does_not_escalate(db_session, monkeypatch):
    """Escalation requires BOTH negative sentiment AND complaint
    intent — a merely negative-toned support request shouldn't
    over-trigger escalation."""
    _patch_crm_writes(monkeypatch)

    result = await concierge_agent.evaluate_message(
        db_session, TENANT, "msg-5", "conv-5", "I'm so frustrated, I need help figuring out the app.",
        client=_UnconfiguredClient(),
    )
    assert result.sentiment == "negative"
    assert result.intent == "support_request"
    assert result.escalated is False


async def test_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=concierge_agent.AGENT_KEY, display_name="Concierge Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await concierge_agent.evaluate_message(db_session, TENANT, "msg-6", "conv-6", "Hello", client=_UnconfiguredClient())
    assert result.sentiment is None
    assert result.skipped_reason == "paused"


async def test_decision_count_increments(db_session, monkeypatch):
    _patch_crm_writes(monkeypatch)
    agent_row = Agent(tenant_id=TENANT, key=concierge_agent.AGENT_KEY, display_name="Concierge Agent")
    db_session.add(agent_row)
    await db_session.commit()

    await concierge_agent.evaluate_message(db_session, TENANT, "msg-7", "conv-7", "Hi there", client=_UnconfiguredClient())
    await db_session.refresh(agent_row)
    assert agent_row.decisions_made == 1


async def test_live_claude_path_uses_submit_tool(db_session, monkeypatch):
    monkeypatch.setattr(
        crm_tools, "build_message_assessment_tool_dispatch",
        lambda tenant_id: {"get_conversation_history": lambda inp: {"messages": []}, "submit_message_assessment": lambda inp: {}},
    )
    _patch_crm_writes(monkeypatch, draft_id="draft-live-1")

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Clearly frustrated, needs a human.",
                tool_calls=[
                    ToolCallResult(tool_name="get_conversation_history", tool_input={"conversation_id": "conv-8"}, tool_output={"messages": []}),
                    ToolCallResult(
                        tool_name="submit_message_assessment",
                        tool_input={"sentiment": "negative", "intent": "complaint", "escalate": True, "draft_reply": "I'm so sorry — let me get this fixed right away.", "reasoning": "customer sarcasm signals real frustration despite polite wording"},
                        tool_output={"sentiment": "negative", "intent": "complaint", "escalate": True, "draft_reply": "I'm so sorry — let me get this fixed right away.", "reasoning": "customer sarcasm signals real frustration despite polite wording"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await concierge_agent.evaluate_message(db_session, TENANT, "msg-8", "conv-8", "Oh great, ANOTHER cancellation. Just wonderful.", client=_FakeClient())
    assert result.sentiment == "negative"
    assert result.escalated is True
    assert result.draft_reply_id == "draft-live-1"


async def test_no_draft_created_when_agent_produces_empty_reply(db_session, monkeypatch):
    monkeypatch.setattr(crm_tools, "set_message_sentiment_intent", lambda *a, **k: {})
    create_calls = []
    monkeypatch.setattr(crm_tools, "create_draft_reply", lambda *a, **k: create_calls.append(a) or {})
    monkeypatch.setattr(crm_tools, "escalate_conversation", lambda *a, **k: {})
    monkeypatch.setattr(
        crm_tools, "build_message_assessment_tool_dispatch",
        lambda tenant_id: {"get_conversation_history": lambda inp: {"messages": []}, "submit_message_assessment": lambda inp: {}},
    )

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(final_text="no tool call", tool_calls=[], stop_reason="end_turn")

    result = await concierge_agent.evaluate_message(db_session, TENANT, "msg-9", "conv-9", "Hi", client=_FakeClient())
    assert result.draft_reply_id is None
    assert len(create_calls) == 0


async def test_handle_message_received_adapter(db_session, monkeypatch):
    _patch_crm_writes(monkeypatch)

    result = await concierge_agent.handle_message_received(
        db_session, TENANT, "crm.message_received", {"message_id": "msg-10", "conversation_id": "conv-10", "body": "What's the price?"}
    )
    assert result.intent == "sales_inquiry"
