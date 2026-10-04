"""
Concierge Agent — CRM-003 AI Conversational Engagement Hub.

Reacts to crm.message_received automatically, the moment crm-svc
publishes it for every inbound message — not on request. Produces
three things every time: a sentiment/intent read, an escalation
decision, and a drafted reply. It never sends that reply itself —
create_draft_reply always writes PENDING_REVIEW, exactly like
marketing-svc's Marketing Content Agent and its ContentDraft. There is
no tool this agent has that can send a message to a customer.

Two-layer discipline:
1. Deterministic fallback — real keyword-based sentiment/intent
   detection and a fixed, honest, functional draft template. Never
   creative, which is also why it can never accidentally say something
   inappropriate: the templates are fixed strings, chosen only by
   intent category.
2. Live Claude path — reads real conversation history via tool use
   before drafting, catches nuance (sarcasm, mixed sentiment, an
   implicit ask) the keyword pass can't.
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.anthropic_client import AnthropicAgentClient
from src.models import Agent, AgentStatus, DecisionLog
from src.services import crm_tools

AGENT_KEY = "concierge_agent"

MESSAGE_ASSESSMENT_SYSTEM_PROMPT = """You are the Concierge Agent for VOYQI's Enterprise \
CRM, assessing an inbound customer message (CRM-003).

Use get_conversation_history to see the full thread before drafting — a reply written \
in isolation from prior context reads as tone-deaf. Determine sentiment \
(positive/neutral/negative), intent (complaint/sales_inquiry/support_request/general), \
and whether this needs human escalation — negative sentiment paired with a complaint, \
or anything suggesting real urgency (safety, a stranded member, a security concern), \
should escalate. Draft a genuinely helpful, warm reply — but you are never sending \
this yourself; a human always reviews before it goes out, so don't hedge the draft \
into uselessness, write the reply you'd actually want a rep to be able to just approve. \
Always finish by calling submit_message_assessment."""

_NEGATIVE_KEYWORDS = {"angry", "furious", "terrible", "awful", "unacceptable", "refund", "cancel", "worst", "frustrated", "disappointed", "ridiculous"}
_POSITIVE_KEYWORDS = {"great", "thanks", "thank you", "awesome", "love", "happy", "excellent", "appreciate"}
_COMPLAINT_KEYWORDS = {"refund", "cancel", "complaint", "broken", "not working", "unacceptable", "angry", "never showed"}
_SALES_KEYWORDS = {"price", "pricing", "upgrade", "interested", "purchase", "buy", "subscribe", "cost"}
_SUPPORT_KEYWORDS = {"help", "issue", "problem", "broken", "not working", "error", "support", "stuck"}

_DETERMINISTIC_REPLIES = {
    "complaint": "Thank you for reaching out, and I'm sorry to hear about this experience. A member of our team will follow up with you shortly to make this right.",
    "sales_inquiry": "Thanks for your interest in VOYQI! A member of our team will follow up shortly with more information.",
    "support_request": "Thanks for reaching out. We've received your message and a support specialist will assist you shortly.",
    "general": "Thank you for your message. We'll get back to you shortly.",
}


async def _agent_status(db: AsyncSession, tenant_id: str) -> AgentStatus:
    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    return agent_row.status if agent_row else AgentStatus.ACTIVE


def _deterministic_assessment(body: str) -> tuple[str, str, bool, str, str]:
    lower = body.lower()

    sentiment = "neutral"
    if any(kw in lower for kw in _NEGATIVE_KEYWORDS):
        sentiment = "negative"
    elif any(kw in lower for kw in _POSITIVE_KEYWORDS):
        sentiment = "positive"

    if any(kw in lower for kw in _COMPLAINT_KEYWORDS):
        intent = "complaint"
    elif any(kw in lower for kw in _SALES_KEYWORDS):
        intent = "sales_inquiry"
    elif any(kw in lower for kw in _SUPPORT_KEYWORDS):
        intent = "support_request"
    else:
        intent = "general"

    escalate = sentiment == "negative" and intent == "complaint"
    draft_reply = _DETERMINISTIC_REPLIES[intent]
    reasoning = (
        f"[deterministic fallback — ANTHROPIC_API_KEY not configured] keyword-based read: "
        f"sentiment={sentiment}, intent={intent}"
        + (", escalating (negative sentiment + complaint intent)" if escalate else ", no escalation criteria met")
    )
    return sentiment, intent, escalate, draft_reply, reasoning


@dataclass
class MessageAssessmentResult:
    decision_log: DecisionLog
    sentiment: str | None
    intent: str | None
    escalated: bool
    draft_reply_id: str | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_message(
    db: AsyncSession,
    tenant_id: str,
    message_id: str,
    conversation_id: str,
    body: str,
    client: AnthropicAgentClient | None = None,
) -> MessageAssessmentResult:
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="crm.message_received",
            trigger_context={"message_id": message_id, "conversation_id": conversation_id},
            reasoning=f"Agent status is {status.value} — no message assessment was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return MessageAssessmentResult(decision_log=log, sentiment=None, intent=None, escalated=False, draft_reply_id=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = crm_tools.build_message_assessment_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=MESSAGE_ASSESSMENT_SYSTEM_PROMPT,
            user_message=f"Conversation {conversation_id}. New inbound message: \"{body}\". Assess and draft a reply.",
            tools=crm_tools.MESSAGE_ASSESSMENT_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_message_assessment"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            sentiment = submitted.get("sentiment", "neutral")
            intent = submitted.get("intent", "general")
            escalate = submitted.get("escalate", False)
            draft_reply = submitted.get("draft_reply", "")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            sentiment, intent, escalate, draft_reply = "neutral", "general", False, ""
            reasoning = turn.final_text or "(agent produced no final text and never called submit_message_assessment)"
    else:
        sentiment, intent, escalate, draft_reply, reasoning = _deterministic_assessment(body)
        tools_called = [{"tool": "deterministic_keyword_match", "input": {}}]

    crm_tools.set_message_sentiment_intent(tenant_id, message_id, sentiment, intent)
    tools_called.append({"tool": "set_message_sentiment_intent", "input": {"message_id": message_id, "sentiment": sentiment, "intent": intent}})

    draft_response = crm_tools.create_draft_reply(tenant_id, conversation_id, message_id, draft_reply, reasoning) if draft_reply else {}
    draft_id = draft_response.get("id")
    if draft_id:
        tools_called.append({"tool": "create_draft_reply", "input": {"conversation_id": conversation_id, "message_id": message_id}})

    if escalate:
        crm_tools.escalate_conversation(tenant_id, conversation_id, "urgent" if intent == "complaint" and sentiment == "negative" else "high", "support_escalations")
        tools_called.append({"tool": "escalate_conversation", "input": {"conversation_id": conversation_id}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="crm.message_received",
        trigger_context={"message_id": message_id, "conversation_id": conversation_id, "body": body},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="message_assessed_and_drafted" if draft_id else "message_assessed_no_draft",
        outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return MessageAssessmentResult(decision_log=log, sentiment=sentiment, intent=intent, escalated=escalate, draft_reply_id=draft_id, reasoning=reasoning)


async def handle_message_received(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> MessageAssessmentResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_message(db, tenant_id, payload.get("message_id", ""), payload.get("conversation_id", ""), payload.get("body", ""))
