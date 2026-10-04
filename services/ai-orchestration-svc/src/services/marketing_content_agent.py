"""
Marketing Content Agent — MKT-008 AI Content & Campaign Studio.

Unlike the Compliance/Audit Agent (reactive: scores risk on events that
already happened), this agent is generative: given a campaign and a
brief, it drafts customer-facing copy. Two things keep that safe
without requiring a human to review every draft:

1. marketing-svc's own deterministic guardrail (brand-voice/compliance
   keyword checks, channel length limits) runs on every draft the
   instant it's created, independent of whatever the live model was
   asked to do. A clean draft is auto-approved in that same call; one
   that trips the guardrail is held for a human, with the specific
   findings attached. This agent has no way to force either outcome —
   whichever one marketing-svc's guardrail produces is what happens.
2. This agent has no tool that can itself approve a held draft. It can
   propose (create_content_draft) and, for a draft that already
   cleared the guardrail, move it forward (schedule_send,
   schedule_social_post) — but a HELD_FOR_REVIEW draft stays held until
   a human clears it through marketing-svc's own review endpoint,
   which lives outside this agent's tool set entirely.

Same two-layer pattern as compliance_audit_agent: a deterministic
fallback that works with zero API key configured (a simple templated
draft — functional, not creative), and a live Claude tool-use pass when
ANTHROPIC_API_KEY is set. Both paths are logged identically.
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.anthropic_client import AnthropicAgentClient
from src.models import Agent, AgentStatus, DecisionLog
from src.services import marketing_tools

AGENT_KEY = "marketing_content_agent"

SYSTEM_PROMPT = """You are the Marketing Content Agent for VOYQI Mobility's AI Content \
& Campaign Studio.

Brand voice: premium, confident, never desperate. VOYQI sells Portable \
Mobility(TM) — access, not ownership. Refer to members, not customers or \
users. Never use absolute claims like "guaranteed," "risk-free," or \
"best in the world" — these are compliance violations, not style choices. \
If you mention financing, APR, or credit, you must include a disclosure \
that it is subject to credit approval. Respect channel constraints: SMS \
and push copy must be short and scannable, email and social can be fuller.

Use get_campaign_context to see the actual campaign (name, channel, \
objective, performance to date) before writing anything specific to \
it. Use create_content_draft to submit your draft — marketing-svc's \
own guardrail auto-approves a clean draft immediately; only a draft \
that trips the guardrail waits on a human."""


def _fallback_template(campaign_name: str, channel: str, brief: str) -> tuple[str, str]:
    """Deterministic fallback draft — plain, on-brand-by-construction
    (it never touches the banned-phrase list because it's a fixed
    template), functional rather than creative. Used when no live Claude
    reasoning is available."""
    subject = f"{campaign_name} — your VOYQI update" if channel in ("email",) else ""
    body = (
        f"Your {campaign_name} update: {brief.strip()} "
        f"Enjoy your Portable Mobility experience with VOYQI."
    ).strip()
    return subject, body


@dataclass
class ContentGenerationResult:
    decision_log: DecisionLog
    draft: dict[str, Any] | None
    skipped_reason: str | None = None


async def _agent_status(db: AsyncSession, tenant_id: str) -> AgentStatus:
    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    return agent_row.status if agent_row else AgentStatus.ACTIVE


async def generate_content(
    db: AsyncSession,
    tenant_id: str,
    campaign_id: str,
    campaign_name: str,
    channel: str,
    brief: str,
    client: AnthropicAgentClient | None = None,
) -> ContentGenerationResult:
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="content.draft_requested",
            trigger_context={"campaign_id": campaign_id, "channel": channel},
            reasoning=f"Agent status is {status.value} — no draft was generated.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return ContentGenerationResult(decision_log=log, draft=None, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []
    draft: dict[str, Any] | None = None

    if client.is_configured:
        dispatch = marketing_tools.build_tool_dispatch(tenant_id, AGENT_KEY)
        user_message = (
            f"Campaign: {campaign_name} (id={campaign_id})\nChannel: {channel}\nBrief: {brief}\n"
            "Look up the campaign context, then draft and submit content for this channel."
        )
        turn = client.run_agent_turn(
            system_prompt=SYSTEM_PROMPT,
            user_message=user_message,
            tools=marketing_tools.MARKETING_TOOL_SCHEMAS,
            tool_dispatch=dispatch,
        )
        reasoning = turn.final_text or "(agent produced no final text)"
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        draft_calls = [tc for tc in turn.tool_calls if tc.tool_name == "create_content_draft"]
        draft = draft_calls[-1].tool_output if draft_calls else None
    else:
        subject, body = _fallback_template(campaign_name, channel, brief)
        reasoning = (
            "[deterministic fallback — ANTHROPIC_API_KEY not configured, no live Claude "
            f"drafting was performed] Generated a templated draft for channel={channel}."
        )
        draft = marketing_tools.create_content_draft(
            tenant_id, campaign_id, channel, body, subject, AGENT_KEY
        )
        tools_called = [
            {"tool": "create_content_draft", "input": {"campaign_id": campaign_id, "channel": channel}}
        ]

    outcome = "success"
    action_taken = "draft_auto_approved"
    if draft and draft.get("guardrail_findings"):
        outcome = "deferred"
        action_taken = "draft_held_for_review_by_guardrail"
    elif draft is None:
        outcome = "failed"
        action_taken = "no_draft_produced"

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="content.draft_requested",
        trigger_context={"campaign_id": campaign_id, "channel": channel, "brief": brief},
        reasoning=reasoning, tools_called=tools_called, action_taken=action_taken, outcome=outcome,
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return ContentGenerationResult(decision_log=log, draft=draft)
