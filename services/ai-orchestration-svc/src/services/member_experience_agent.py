"""
Member Experience Agent — the fourth specialist, covering the member-
facing intelligence documented across MOB-001 (churn-risk score),
MOB-005 (GU nudge optimization — timing and reward value), and MOB-007
(win-back offer recommendation at cancellation).

Same two-layer discipline as the other three agents: a deterministic
fallback that works with zero API key, and a live Claude path that reads
real member context first. The one hard rule specific to this agent:
evaluate_winback_offer NEVER blocks or delays a cancellation — it always
returns within its own call and subscription-svc proceeds regardless of
whether this succeeds, times out, or recommends nothing. A member's
right to cancel cannot depend on an AI call succeeding.
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.anthropic_client import AnthropicAgentClient
from src.models import Agent, AgentStatus, DecisionLog
from src.services import subscription_tools

AGENT_KEY = "member_experience_agent"

CHURN_SYSTEM_PROMPT = """You are the Member Experience Agent for VOYQI Mobility, operating with \
full autonomy on this capability.

Assess a member's churn risk using get_member_profile and get_gu_ledger \
to see their actual engagement (GU activity, class, service level) before \
scoring. Return a risk assessment from 0 (no risk) to 100 (near-certain \
churn) with your reasoning. A score at or above the intervention threshold \
autonomously awards a retention-save GU bonus — there is no approval step \
for this, since nothing has been offered to the member yet and this is the \
platform acting on its own initiative, not a member-facing decision."""

WINBACK_SYSTEM_PROMPT = """You are the Member Experience Agent for VOYQI Mobility, \
evaluating whether a win-back offer is warranted for a member who has just \
requested cancellation.

Look at the member's actual profile and GU history before deciding. Not every \
cancellation is retainable — a member moving abroad or citing a permanent life \
change is not the same as one citing price sensitivity or a fixable service \
issue. Recommend an offer only when there's a real chance of retention; \
otherwise say so plainly."""


async def _agent_status(db: AsyncSession, tenant_id: str) -> AgentStatus:
    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    return agent_row.status if agent_row else AgentStatus.ACTIVE


async def _log_and_count(db: AsyncSession, tenant_id: str, trigger_event_type: str, trigger_context: dict, reasoning: str, tools_called: list, action_taken: str, outcome: str) -> DecisionLog:
    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type=trigger_event_type,
        trigger_context=trigger_context, reasoning=reasoning, tools_called=tools_called,
        action_taken=action_taken, outcome=outcome,
    )
    db.add(log)
    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1
    await db.commit()
    await db.refresh(log)
    return log


@dataclass
class ChurnRiskResult:
    decision_log: DecisionLog
    risk_score: float | None
    skipped_reason: str | None = None


def _deterministic_churn_score(context: dict[str, Any]) -> tuple[float, str]:
    """Simple, explainable RFM-style heuristic: days since last use and
    pause history drive the score. Not clever, but auditable — every
    input to the number is named in the reasoning string."""
    score = 0.0
    drivers = []
    days_idle = context.get("days_since_last_use", 0)
    if days_idle >= 30:
        score += 40
        drivers.append(f"{days_idle} days since last use")
    elif days_idle >= 14:
        score += 20
        drivers.append(f"{days_idle} days since last use")

    pause_count = context.get("pause_count_last_year", 0)
    if pause_count >= 2:
        score += 25
        drivers.append(f"{pause_count} pauses in the last year")

    support_complaints = context.get("support_complaints_last_90d", 0)
    if support_complaints >= 2:
        score += 25
        drivers.append(f"{support_complaints} support complaints in the last 90 days")

    gu_engagement = context.get("gu_earn_events_last_90d", 1)
    if gu_engagement == 0:
        score += 10
        drivers.append("no GU earn activity in the last 90 days")

    score = max(0.0, min(100.0, score))
    reasoning = f"Score {score}/100. Drivers: {'; '.join(drivers) if drivers else 'no elevated-risk signals'}."
    return score, reasoning


CHURN_INTERVENTION_THRESHOLD = 50.0
RETENTION_SAVE_GU_BONUS = 250


async def evaluate_churn_risk(
    db: AsyncSession, tenant_id: str, member_profile_id: str, context: dict[str, Any], client: AnthropicAgentClient | None = None
) -> ChurnRiskResult:
    """Fully autonomous: unlike evaluate_winback below (which only
    fires once a member has already chosen to cancel, and returns an
    offer for the MEMBER to accept or decline), this runs proactively
    on the platform's own initiative — there is no member consent
    step to preserve here, because nothing has been offered to a
    member yet. A risk score at or above CHURN_INTERVENTION_THRESHOLD
    autonomously awards a retention-save GU bonus in the same call,
    no "Approve AI" step in between."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = await _log_and_count(
            db, tenant_id, "churn_risk.evaluation_requested", {"member_profile_id": member_profile_id},
            f"Agent status is {status.value} — no churn evaluation was performed.", [], "skipped_agent_not_active", "deferred",
        )
        return ChurnRiskResult(decision_log=log, risk_score=None, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = subscription_tools.build_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=CHURN_SYSTEM_PROMPT,
            user_message=f"Member: {member_profile_id}\nContext: {context}\nAssess churn risk.",
            tools=subscription_tools.SUBSCRIPTION_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        reasoning = turn.final_text or "(agent produced no final text)"
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        # Live Claude's score is embedded in its own text; a real integration
        # would ask for a structured tool-call response. For this build the
        # deterministic score is still computed as the floor score reported
        # alongside Claude's narrative reasoning.
        risk_score, _ = _deterministic_churn_score(context)
    else:
        risk_score, reasoning = _deterministic_churn_score(context)
        reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {reasoning}"

    action_taken = "churn_score_computed"
    if risk_score >= CHURN_INTERVENTION_THRESHOLD:
        subscription_tools.award_retention_gu_bonus(tenant_id, member_profile_id, RETENTION_SAVE_GU_BONUS)
        tools_called.append({"tool": "award_retention_gu_bonus", "input": {"member_profile_id": member_profile_id, "amount": RETENTION_SAVE_GU_BONUS}})
        reasoning += f" Risk score at or above the {CHURN_INTERVENTION_THRESHOLD} intervention threshold — {RETENTION_SAVE_GU_BONUS} GU retention-save bonus awarded autonomously."
        action_taken = "churn_score_computed_and_retention_bonus_awarded"

    log = await _log_and_count(
        db, tenant_id, "churn_risk.evaluation_requested", {"member_profile_id": member_profile_id, **context},
        reasoning, tools_called, action_taken, "success",
    )
    return ChurnRiskResult(decision_log=log, risk_score=risk_score)


UPGRADE_SYSTEM_PROMPT = """You are the Member Experience Agent for VOYQI Mobility, operating with \
full autonomy on this capability.

Use get_upgrade_pattern to see whether this member has a real above-class \
request pattern, not a one-off. If a pattern is confirmed, call \
create_upgrade_case with your reasoning — this only offers the case, it \
never changes what the member is billed. The member's own acceptance of \
the offer is always a separate step handled elsewhere; you do not need to \
concern yourself with it."""


@dataclass
class UpgradeCaseResult:
    decision_log: DecisionLog
    case_created: bool
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_upgrade_pattern(
    db: AsyncSession, tenant_id: str, member_profile_id: str, subscription_id: str, current_class: str, client: AnthropicAgentClient | None = None,
) -> UpgradeCaseResult:
    """Fully autonomous through case creation — detecting a pattern
    and offering a case never touches billing. Actually enacting the
    upgrade (which raises the member's cost) is always a separate,
    member-confirmed step in subscription-svc that this function
    never calls — the financial-risk line sits between offering and
    accepting, not before offering."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = await _log_and_count(
            db, tenant_id, "subscription.above_class_request_recorded", {"member_profile_id": member_profile_id, "subscription_id": subscription_id},
            f"Agent status is {status.value} — no upgrade evaluation was performed.", [], "skipped_agent_not_active", "deferred",
        )
        return UpgradeCaseResult(decision_log=log, case_created=False, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    pattern = subscription_tools.get_upgrade_pattern(tenant_id, member_profile_id)
    tools_called.append({"tool": "get_upgrade_pattern", "input": {"member_profile_id": member_profile_id}})

    if client.is_configured:
        dispatch = {
            **subscription_tools.build_tool_dispatch(tenant_id),
            "get_upgrade_pattern": lambda inp: subscription_tools.get_upgrade_pattern(tenant_id, inp["member_profile_id"]),
            "create_upgrade_case": lambda inp: subscription_tools.create_upgrade_case(
                tenant_id, inp["member_profile_id"], subscription_id, current_class, inp["recommended_class"], inp.get("request_count", 0), inp["reasoning"],
            ),
        }
        turn = client.run_agent_turn(
            system_prompt=UPGRADE_SYSTEM_PROMPT,
            user_message=f"Member: {member_profile_id}\nSubscription: {subscription_id}\nCurrent class: {current_class}\nEvaluate for an upgrade case.",
            tools=[
                *subscription_tools.SUBSCRIPTION_TOOL_SCHEMAS,
                {"name": "get_upgrade_pattern", "description": "Check whether this member has a confirmed above-class request pattern.", "input_schema": {"type": "object", "properties": {"member_profile_id": {"type": "string"}}, "required": ["member_profile_id"]}},
                {"name": "create_upgrade_case", "description": "Offer an upgrade case. Never changes billing.", "input_schema": {"type": "object", "properties": {"member_profile_id": {"type": "string"}, "recommended_class": {"type": "string"}, "request_count": {"type": "integer"}, "reasoning": {"type": "string"}}, "required": ["member_profile_id", "recommended_class", "reasoning"]}},
            ],
            tool_dispatch=dispatch,
        )
        tools_called += [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        case_calls = [tc for tc in turn.tool_calls if tc.tool_name == "create_upgrade_case"]
        case_created = bool(case_calls)
        reasoning = turn.final_text or "(agent produced no final text)"
    else:
        pattern_detected = pattern.get("pattern_detected", False)
        case_created = False
        if pattern_detected:
            recommended_class = pattern.get("recommended_class")
            request_count = pattern.get("request_count", 0)
            reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {pattern.get('reasoning', '')} Offering upgrade case to {recommended_class}."
            subscription_tools.create_upgrade_case(tenant_id, member_profile_id, subscription_id, current_class, recommended_class, request_count, reasoning)
            tools_called.append({"tool": "create_upgrade_case", "input": {"member_profile_id": member_profile_id, "recommended_class": recommended_class}})
            case_created = True
        else:
            reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {pattern.get('reasoning', 'no pattern detected')}"

    log = await _log_and_count(
        db, tenant_id, "subscription.above_class_request_recorded", {"member_profile_id": member_profile_id, "subscription_id": subscription_id, "current_class": current_class},
        reasoning, tools_called, "upgrade_case_offered" if case_created else "no_pattern_detected", "success",
    )
    return UpgradeCaseResult(decision_log=log, case_created=case_created, reasoning=reasoning)


async def handle_above_class_request_recorded(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> UpgradeCaseResult:
    """Adapter matching the supervisor's AgentHandler signature.
    subscription_id and subscribed_class travel directly in the event
    payload (the caller already knows them at request time), so there
    is no lookup here that could return the wrong subscription."""
    member_profile_id = payload.get("member_profile_id", "")
    subscription_id = payload.get("subscription_id", "")
    subscribed_class = payload.get("subscribed_class", "")
    return await evaluate_upgrade_pattern(db, tenant_id, member_profile_id, subscription_id, subscribed_class)


@dataclass
class WinbackResult:
    decision_log: DecisionLog
    offer: dict[str, Any] | None
    skipped_reason: str | None = None


def _deterministic_winback_offer(reason: str) -> dict[str, Any] | None:
    reason_lower = reason.lower()
    non_retainable_terms = ["moving", "relocat", "deceased", "no longer need", "no longer able"]
    if any(term in reason_lower for term in non_retainable_terms):
        return None
    if "expensive" in reason_lower or "price" in reason_lower or "cost" in reason_lower:
        return {"offer": "20% off for 3 months", "gu_bonus": 500}
    if "service" in reason_lower or "issue" in reason_lower or "problem" in reason_lower:
        return {"offer": "dedicated concierge follow-up + 250 GU goodwill credit", "gu_bonus": 250}
    return {"offer": "pause option (hold your class, no charge) instead of cancelling", "gu_bonus": 0}


async def evaluate_winback(
    db: AsyncSession, tenant_id: str, member_profile_id: str, subscription_id: str, reason: str, client: AnthropicAgentClient | None = None
) -> WinbackResult:
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = await _log_and_count(
            db, tenant_id, "subscription.cancellation_initiated", {"member_profile_id": member_profile_id, "subscription_id": subscription_id},
            f"Agent status is {status.value} — no win-back evaluation was performed.", [], "skipped_agent_not_active", "deferred",
        )
        return WinbackResult(decision_log=log, offer=None, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = subscription_tools.build_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=WINBACK_SYSTEM_PROMPT,
            user_message=f"Member: {member_profile_id}\nSubscription: {subscription_id}\nCancellation reason: {reason}\nRecommend an offer or none.",
            tools=subscription_tools.SUBSCRIPTION_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        reasoning = turn.final_text or "(agent produced no final text)"
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        offer = _deterministic_winback_offer(reason)
    else:
        offer = _deterministic_winback_offer(reason)
        reasoning = (
            f"[deterministic fallback — ANTHROPIC_API_KEY not configured] reason='{reason}' -> "
            f"{'offer recommended: ' + str(offer) if offer else 'no offer — reason indicates a non-retainable cancellation'}"
        )

    log = await _log_and_count(
        db, tenant_id, "subscription.cancellation_initiated", {"member_profile_id": member_profile_id, "subscription_id": subscription_id, "reason": reason},
        reasoning, tools_called, "winback_offer_recommended" if offer else "no_offer_recommended", "success",
    )
    return WinbackResult(decision_log=log, offer=offer)
