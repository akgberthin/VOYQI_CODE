"""
Compliance/Audit Agent — Super Admin Panel module.

Reacts to rbac.role_assignments and rbac.permission_overrides events, and
now hub-network security events too. Two layers of reasoning, both real
and both logged:

1. A deterministic risk score, always computed, never skipped — this is
   what lets the agent function correctly in CI and in any environment
   without live model credentials, and it's also the floor Claude's
   judgment is checked against, not replaced by.
2. A live Claude reasoning pass (when ANTHROPIC_API_KEY is configured)
   that pulls domain-relevant context via tool use before producing the
   actual natural-language reasoning stored in DecisionLog.reasoning —
   RBAC context for identity/access events, and a card's real tap
   history for hub-security events (was this the member's first
   anomaly, or a repeat pattern? do the two hubs involved match places
   the member normally visits?). When no key is configured, the
   deterministic reasoning is stored instead and is labeled as such —
   never silently substituted for a live decision.

Outcome: recommendations at or above the agent's human_approval_threshold
become a PENDING AIRecommendation (AI-002 review queue). Below threshold,
the agent may auto-execute (currently: auto-revoking a permission grant it
assesses as clearly unwarranted) and the decision is still fully logged.
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.anthropic_client import AnthropicAgentClient
from src.core.tiering import get_tier
from src.models import Agent, AIRecommendation, DecisionLog, RecommendationStatus
from src.services import hub_tools, org_tools, rbac_tools, subscription_tools

HUB_SECURITY_EVENT_TYPES = {"card.geographic_anomaly_detected"}
ORG_COMPLIANCE_EVENT_TYPES = {"org.decision_finalized"}
DEPOSIT_EVENT_TYPES = {"deposit.capture_requested"}
DISPARATE_IMPACT_MIN_SAMPLE_SIZE = 5
DISPARATE_IMPACT_REVIEW_SPREAD_PCT = 20.0
DISPARATE_IMPACT_MONITOR_SPREAD_PCT = 10.0

AGENT_KEY = "compliance_audit_agent"

SYSTEM_PROMPT = """You are the Compliance/Audit Agent for VOYQI's Super Admin Panel.

You review RBAC events (role assignments, permission grants) for risk.
Use the get_user_rbac_context tool to see the user's full current access
footprint before judging. Consider: whether the permission is sensitive,
who granted it, whether the grant was itself AI-initiated, and whether the
user's combined access looks like unwarranted privilege escalation.

Respond with a short, concrete risk assessment. If you believe an action
should be taken (e.g. a grant should be revoked), say so explicitly, but do
not call revoke_permission_override yourself unless the case is
unambiguous — prefer flagging for human review when in doubt."""


def _deterministic_risk_score(
    event_type: str, payload: dict, context: dict | None = None
) -> tuple[float, list[str]]:
    """The always-on risk heuristic. Returns (score 0-100, driver list).

    `context` is the user's live RBAC footprint (rbac_tools.get_user_rbac_context
    result), fetched by the caller. It's optional and best-effort: if the
    lookup failed (rbac-svc unreachable), the event-payload-only signals
    below still run — accumulation detection just doesn't fire that pass,
    it never blocks the evaluation."""
    score = 0.0
    drivers: list[str] = []

    if event_type == "permission.granted":
        if payload.get("is_sensitive"):
            score += 60
            drivers.append("permission is on the sensitive-permission list")
        if payload.get("effect") == "deny":
            score -= 20
            drivers.append("effect is DENY (defensive, lower risk than ALLOW)")
        if payload.get("ai_initiated"):
            score += 15
            drivers.append("grant was itself AI-initiated")
        granted_by = str(payload.get("granted_by", ""))
        if "ai-orchestration" in granted_by and payload.get("is_sensitive"):
            score += 15
            drivers.append("sensitive permission granted by an AI agent, not a human")
        if payload.get("user_id") and granted_by and payload["user_id"] == granted_by:
            score += 55
            drivers.append("user granted this permission to themselves (self-grant)")
    elif event_type == "role.assigned":
        if payload.get("ai_initiated"):
            score += 25
            drivers.append("role assignment was AI-initiated")
        assigned_by = str(payload.get("assigned_by", ""))
        if payload.get("user_id") and assigned_by and payload["user_id"] == assigned_by:
            score += 55
            drivers.append("user assigned this role to themselves (self-grant)")
    elif event_type == "abac_policy.activated":
        if payload.get("is_sensitive") and payload.get("effect") == "allow":
            score += 55
            drivers.append("ABAC policy grants access to a sensitive permission")
        if payload.get("had_conflicts_forced"):
            score += 55
            drivers.append("policy was force-activated over a detected RBAC/ABAC conflict")
        if payload.get("ai_initiated"):
            score += 15
            drivers.append("policy was itself AI-initiated")
    elif event_type == "panel.granted":
        if payload.get("is_sensitive"):
            score += 55
            drivers.append("grant targets a sensitive panel (AI Orchestration, Security, or Capital)")
        if payload.get("ai_initiated"):
            score += 15
            drivers.append("panel grant was itself AI-initiated")
    elif event_type == "access.break_glass_activated":
        # Break-glass is emergency access by definition — it starts at an
        # elevated baseline risk and is *raised* further by off-hours
        # timing or an unusually long window, never lowered to zero.
        score += 60
        drivers.append("break-glass emergency access was activated")
        if payload.get("off_hours"):
            score += 20
            drivers.append("activated outside business hours")
        if payload.get("duration_minutes", 0) >= 45:
            score += 15
            drivers.append(f"long duration window ({payload.get('duration_minutes')} min)")
    elif event_type == "access.impersonation_started":
        score += 50
        drivers.append("impersonation session started")
        if payload.get("off_hours"):
            score += 20
            drivers.append("started outside business hours")
        if not payload.get("has_session_recording"):
            score += 25
            drivers.append("no session recording reference attached")
    elif event_type == "feature_flag.kill_switch_toggled":
        if payload.get("kill_switch_armed"):
            score += 50
            drivers.append(f"kill switch armed, disabling agent '{payload.get('target_agent_key')}'")
            if payload.get("target_agent_key") == AGENT_KEY:
                score += 30
                drivers.append(
                    "the Compliance/Audit Agent is disabling ITSELF — no agent oversight "
                    "will exist for subsequent events until this is reversed"
                )
        if not payload.get("agent_action_applied"):
            score += 20
            drivers.append("target agent was not found in ai-orchestration-svc — kill switch had no effect")
    elif event_type == "content.published":
        if payload.get("content_type") == "legal_disclosure" and payload.get("bypassed_review"):
            score += 65
            drivers.append("legal disclosure published without going through the review workflow")
    elif event_type == "role.privilege_drift_detected":
        excess_count = payload.get("excess_permission_count", 0)
        if excess_count >= 3:
            score += 55
            drivers.append(
                f"role holds {excess_count} permissions beyond its least-privilege template — "
                "significant drift"
            )
        elif excess_count >= 1:
            score += 30
            drivers.append(f"role holds {excess_count} permission(s) beyond its least-privilege template")
    elif event_type == "content_draft.approved_with_violations":
        violation_count = payload.get("violation_count", 0)
        score += 55
        drivers.append(
            f"marketing content approved for send despite {violation_count} guardrail violation(s)"
        )
        if violation_count >= 2:
            score += 20
            drivers.append("multiple guardrail violations overridden in a single approval")
    elif event_type == "voice_sms.quiet_hours_override":
        score += 55
        drivers.append(
            f"TCPA quiet-hours override used to schedule outreach at hour "
            f"{payload.get('recipient_local_hour')}"
        )
    elif event_type == "push.frequency_cap_overridden":
        score += 45
        drivers.append(f"frequency cap overridden for recipient {payload.get('recipient_id')}")
    elif event_type == "asset.expired_usage_override":
        score += 55
        drivers.append(
            f"content attached an asset whose usage rights had already expired "
            f"(override reason: {payload.get('override_reason', '(none given)')})"
        )
    elif event_type == "gu.fraud_suspected":
        score += 55
        drivers.append(
            f"member exceeded the GU earn-rate threshold ({payload.get('trigger')} trigger) — "
            "possible abuse of the incentive economy"
        )
    elif event_type == "deposit.capture_requested":
        score += 55
        drivers.append(
            f"deposit capture requested (${payload.get('amount_usd', 0)}, reason: {payload.get('reason', '(none given)')}) — "
            "discretionary money taken from a member always crosses the default review threshold, matching gu.fraud_suspected's pattern"
        )
        hold_amount = payload.get("hold_amount_usd")
        capture_amount = payload.get("amount_usd", 0)
        if hold_amount and capture_amount and capture_amount >= 0.75 * hold_amount:
            score += 20
            drivers.append(f"capture amount (${capture_amount}) is ≥75% of the full ${hold_amount} hold — near-total forfeiture")
    elif event_type == "incident.total_loss_declared":
        score += 55
        drivers.append(
            f"vehicle declared total loss (reason: {payload.get('reason', '(none given)')}) — "
            "financially material write-off, always audited regardless of whether anything looks wrong"
        )
    elif event_type == "vehicle.tamper_detected":
        score += 60
        drivers.append(
            f"device tamper signal detected on a fleet vehicle ({payload.get('detected_signal', '(none given)')}) — "
            "security concern on a high-value asset, always reviewed"
        )
    elif event_type == "floorplan.covenant_breached":
        score += 55
        drivers.append(
            f"floorplan facility utilization covenant breached by pledging vehicle "
            f"{payload.get('vehicle_id')} — financial covenant risk, always reviewed"
        )
    elif event_type == "adverse_action.notice_overdue":
        score += 65
        drivers.append(
            f"adverse action notice ({payload.get('reason', '(none given)')}) went past its deadline "
            "without being issued — real FCRA/ECOA fair-lending exposure, always reviewed"
        )
    elif event_type == "red_flag.check_overridden":
        flag_count = len(payload.get("flags_detected", []))
        score += 60
        drivers.append(
            f"identity-theft red-flag check overridden despite {flag_count} flag(s) detected — "
            "always reviewed regardless of the stated justification"
        )
    elif event_type == "franchise.facility_non_compliant":
        score += 50
        drivers.append(
            f"rooftop facility/image program marked non-compliant (notes: {payload.get('notes', '(none given)')}) — "
            "franchise-agreement exposure"
        )
    elif event_type == "credit.ofac_match_detected":
        score += 75
        drivers.append(
            f"OFAC sanctions match on bureau pull ({payload.get('bureau', '(unknown bureau)')}) — "
            "federal sanctions screening, the application is permanently blocked with no override path, "
            "always reviewed at the highest severity"
        )
    elif event_type == "vhr.title_issue_disclosure_overridden":
        score += 60
        drivers.append(
            f"vehicle {payload.get('vin', '(unknown VIN)')} listed with a title issue override before "
            f"disclosure was acknowledged (reason: {payload.get('override_reason', '(none given)')}) — "
            "FTC Used Car Rule exposure, always reviewed regardless of the stated justification"
        )
    elif event_type == "auction.overbid_book_override":
        score += 50
        drivers.append(
            f"auction bid ${payload.get('max_bid_usd', 0):,.0f} placed above the VIN's average wholesale "
            f"book value with a documented override (reason: {payload.get('override_reason', '(none given)')}) — "
            "legitimate business judgment (presold units, rare specs), but systematic overbidding erodes "
            "gross per unit, so every instance is reviewed"
        )
    elif event_type == "card.geographic_anomaly_detected":
        score += 65
        drivers.append(
            f"VOYQI Card tap failed geographic plausibility check ({payload.get('reason', '(no detail)')}) — "
            "potential card theft or cloning, the card has already been auto-suspended as the safe default; "
            "this review is to verify the member and reactivate, or confirm fraud and escalate"
        )
    elif event_type == "org.decision_finalized":
        snapshot = context or {}
        groups = [g for g in snapshot.get("groups", []) if g.get("total", 0) >= DISPARATE_IMPACT_MIN_SAMPLE_SIZE]
        if len(groups) >= 2:
            rates = [g["decline_rate_pct"] for g in groups]
            spread = max(rates) - min(rates)
            if spread >= DISPARATE_IMPACT_REVIEW_SPREAD_PCT:
                score += 55
                drivers.append(
                    f"decline-rate spread of {spread:.1f} points across origination types with adequate sample "
                    f"size (max {max(rates):.1f}%, min {min(rates):.1f}%) — worth a fair-lending review; business "
                    "variables only, no protected-class data exists in this system to have driven it"
                )
            elif spread >= DISPARATE_IMPACT_MONITOR_SPREAD_PCT:
                score += 25
                drivers.append(
                    f"moderate decline-rate spread of {spread:.1f} points across origination types — below the "
                    "review threshold but worth continued monitoring as more decisions accumulate"
                )
            else:
                drivers.append(f"decline-rate spread of {spread:.1f} points across origination types is within normal range")
        else:
            drivers.append("insufficient sample size across origination-type groups yet to assess disparate impact meaningfully")

    if context and event_type not in ORG_COMPLIANCE_EVENT_TYPES and event_type not in HUB_SECURITY_EVENT_TYPES:
        total_active = len(context.get("active_roles", [])) + len(context.get("active_overrides", []))
        if total_active >= 5:
            score += 55
            drivers.append(
                f"user now holds {total_active} active roles/overrides — rapid privilege accumulation"
            )

    score = max(0.0, min(100.0, score))
    if not drivers:
        drivers.append("no elevated-risk signals detected")
    return score, drivers


@dataclass
class ComplianceEvaluation:
    decision_log: DecisionLog
    recommendation: AIRecommendation | None


# The human decision a review card asks for belongs to the policy action
# it would act on. Tamper events, fair-lending outcomes and ABAC access
# policies are tier 3 (named accountable role); every other review is a
# tier-2 approval.
REVIEW_POLICY_ACTIONS: dict[str, tuple[str, str]] = {
    "vehicle.tamper_detected": ("fleet-intelligence", "telematics.tamper_event_flag"),
    "org.decision_finalized": ("sales-originations-intelligence", "credit.decision"),
    "abac_policy.activated": ("compliance-risk-intelligence", "abac.policy_grant_apply"),
}


def _review_tier(event_type: str) -> int:
    action = REVIEW_POLICY_ACTIONS.get(event_type)
    return max(get_tier(*action), 2) if action else 2


async def evaluate_event(
    db: AsyncSession,
    tenant_id: str,
    event_type: str,
    payload: dict[str, Any],
    client: AnthropicAgentClient | None = None,
) -> ComplianceEvaluation:
    client = client or AnthropicAgentClient()

    context: dict | None = None
    user_id = payload.get("user_id")
    if event_type in ORG_COMPLIANCE_EVENT_TYPES:
        try:
            context = org_tools.get_disparate_impact_snapshot(tenant_id)
        except Exception:
            context = None
    elif event_type in DEPOSIT_EVENT_TYPES and payload.get("hold_id"):
        try:
            hold = subscription_tools.get_deposit_hold(tenant_id, payload["hold_id"])
            context = hold
            payload = {**payload, "hold_amount_usd": hold.get("amount_usd")}
        except Exception:
            # subscription-svc unreachable — proceed on the payload's own
            # amount alone rather than blocking the review entirely.
            context = None
    elif user_id:
        try:
            context = rbac_tools.get_user_rbac_context(tenant_id, user_id)
        except Exception:
            # rbac-svc unreachable or user not found — proceed without the
            # accumulation signal rather than failing the whole evaluation.
            context = None

    risk_score, drivers = _deterministic_risk_score(event_type, payload, context)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    threshold = agent_row.human_approval_threshold if agent_row else 50

    tools_called: list[dict] = []
    reasoning: str

    if client.is_configured:
        if event_type in HUB_SECURITY_EVENT_TYPES:
            dispatch = hub_tools.build_card_tap_history_tool_dispatch(tenant_id)
            user_message = (
                f"Event: {event_type}\nPayload: {payload}\n"
                f"Deterministic risk pre-score: {risk_score}/100 ({'; '.join(drivers)}).\n"
                "Look up this card's real tap history and assess whether this looks like a first-time "
                "anomaly, a repeat pattern, or plausibly explained by the member's normal travel."
            )
            turn = client.run_agent_turn(
                system_prompt=SYSTEM_PROMPT,
                user_message=user_message,
                tools=hub_tools.CARD_TAP_HISTORY_TOOL_SCHEMAS,
                tool_dispatch=dispatch,
            )
        elif event_type in ORG_COMPLIANCE_EVENT_TYPES:
            dispatch = org_tools.build_disparate_impact_tool_dispatch(tenant_id)
            user_message = (
                f"Event: {event_type}\nPayload: {payload}\n"
                f"Deterministic risk pre-score: {risk_score}/100 ({'; '.join(drivers)}).\n"
                "Look up the current disparate-impact snapshot (business variables only — origination type, "
                "never protected-class data) and assess whether the pattern genuinely warrants a fair-lending review."
            )
            turn = client.run_agent_turn(
                system_prompt=SYSTEM_PROMPT,
                user_message=user_message,
                tools=org_tools.DISPARATE_IMPACT_TOOL_SCHEMAS,
                tool_dispatch=dispatch,
            )
        else:
            dispatch = rbac_tools.build_tool_dispatch(tenant_id)
            user_message = (
                f"Event: {event_type}\nPayload: {payload}\n"
                f"Deterministic risk pre-score: {risk_score}/100 ({'; '.join(drivers)}).\n"
                "Look up the user's RBAC context and give your assessment."
            )
            turn = client.run_agent_turn(
                system_prompt=SYSTEM_PROMPT,
                user_message=user_message,
                tools=rbac_tools.RBAC_TOOL_SCHEMAS,
                tool_dispatch=dispatch,
            )
        reasoning = turn.final_text or "(agent produced no final text)"
        tools_called = [
            {"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls
        ]
    else:
        reasoning = (
            "[deterministic fallback — ANTHROPIC_API_KEY not configured, no live Claude "
            f"reasoning was performed] Risk score {risk_score}/100. Drivers: {'; '.join(drivers)}."
        )

    recommendation: AIRecommendation | None = None
    outcome: str
    action_taken: str

    if risk_score >= threshold:
        if event_type == "permission.granted":
            action_type = "review_permission_grant"
        elif event_type == "role.assigned":
            action_type = "review_role_assignment"
        elif event_type == "panel.granted":
            action_type = "review_panel_grant"
        elif event_type == "access.break_glass_activated":
            action_type = "review_break_glass_session"
        elif event_type == "access.impersonation_started":
            action_type = "review_impersonation_session"
        elif event_type == "feature_flag.kill_switch_toggled":
            action_type = "review_kill_switch_toggle"
        elif event_type == "content.published":
            action_type = "review_content_publication"
        elif event_type == "role.privilege_drift_detected":
            action_type = "review_privilege_drift"
        elif event_type == "content_draft.approved_with_violations":
            action_type = "review_content_draft_override"
        elif event_type == "voice_sms.quiet_hours_override":
            action_type = "review_quiet_hours_override"
        elif event_type == "push.frequency_cap_overridden":
            action_type = "review_frequency_cap_override"
        elif event_type == "asset.expired_usage_override":
            action_type = "review_expired_asset_usage"
        elif event_type == "incident.total_loss_declared":
            action_type = "review_total_loss_declaration"
        elif event_type == "vehicle.tamper_detected":
            action_type = "review_tamper_event"
        elif event_type == "floorplan.covenant_breached":
            action_type = "review_covenant_breach"
        elif event_type == "gu.fraud_suspected":
            action_type = "review_gu_fraud_flag"
        elif event_type == "deposit.capture_requested":
            action_type = "review_deposit_capture"
        elif event_type == "adverse_action.notice_overdue":
            action_type = "review_adverse_action_overdue"
        elif event_type == "red_flag.check_overridden":
            action_type = "review_red_flag_override"
        elif event_type == "franchise.facility_non_compliant":
            action_type = "review_franchise_non_compliance"
        elif event_type == "credit.ofac_match_detected":
            action_type = "review_ofac_match"
        elif event_type == "vhr.title_issue_disclosure_overridden":
            action_type = "review_title_disclosure_override"
        elif event_type == "auction.overbid_book_override":
            action_type = "review_auction_overbid"
        elif event_type == "card.geographic_anomaly_detected":
            action_type = "review_card_geographic_anomaly"
        elif event_type == "org.decision_finalized":
            action_type = "review_disparate_impact_pattern"
        else:
            action_type = "review_abac_policy"

        domain_map = {
            "access.break_glass_activated": "identity",
            "access.impersonation_started": "identity",
            "feature_flag.kill_switch_toggled": "platform_config",
            "content.published": "cms",
            "role.privilege_drift_detected": "rbac",
            "content_draft.approved_with_violations": "marketing",
            "voice_sms.quiet_hours_override": "marketing",
            "push.frequency_cap_overridden": "marketing",
            "asset.expired_usage_override": "marketing",
            "incident.total_loss_declared": "fleet",
            "vehicle.tamper_detected": "fleet",
            "floorplan.covenant_breached": "fleet",
            "gu.fraud_suspected": "subscription",
            "deposit.capture_requested": "subscription",
            "adverse_action.notice_overdue": "dms",
            "red_flag.check_overridden": "dms",
            "franchise.facility_non_compliant": "dms",
            "credit.ofac_match_detected": "dms",
            "vhr.title_issue_disclosure_overridden": "dms",
            "auction.overbid_book_override": "dms",
            "card.geographic_anomaly_detected": "hub",
            "org.decision_finalized": "org",
        }
        domain = domain_map.get(event_type, "rbac")

        review_tier = _review_tier(event_type)
        recommendation = AIRecommendation(
            tenant_id=tenant_id,
            agent_key=AGENT_KEY,
            domain=domain,
            action_type=action_type,
            proposed_action={"payload": payload, "suggested_action": "revoke_if_unwarranted"},
            reasoning=reasoning,
            risk_score=risk_score,
            drivers=drivers,
            external_record_id=(
                payload.get("override_id")
                or payload.get("assignment_id")
                or payload.get("policy_id")
                or payload.get("grant_id")
                or payload.get("session_id")
                or payload.get("flag_id")
                or payload.get("content_id")
                or payload.get("role_id")
                or payload.get("draft_id")
                or payload.get("send_id")
                or payload.get("push_send_id")
                or payload.get("attachment_id")
                or payload.get("incident_id")
                or payload.get("event_id")
                or payload.get("facility_id")
                or payload.get("ledger_entry_id")
                or payload.get("hold_id")
                or payload.get("notice_id")
                or payload.get("check_id")
                or payload.get("bid_id")
                or payload.get("tap_id")
                or payload.get("decision_id")
                or payload.get("rooftop_id")
                or payload.get("credit_application_id")
                or payload.get("vehicle_id")
            ),
            status=RecommendationStatus.PENDING,
            triggering_event_type=event_type,
            tier=review_tier,
            requires_accountable_role=review_tier == 3,
            triggering_event_id=(
                payload.get("override_id")
                or payload.get("assignment_id")
                or payload.get("policy_id")
                or payload.get("grant_id")
                or payload.get("session_id")
                or payload.get("flag_id")
                or payload.get("content_id")
                or payload.get("role_id")
                or payload.get("draft_id")
                or payload.get("send_id")
                or payload.get("push_send_id")
                or payload.get("attachment_id")
                or payload.get("incident_id")
                or payload.get("event_id")
                or payload.get("facility_id")
                or payload.get("ledger_entry_id")
                or payload.get("hold_id")
                or payload.get("notice_id")
                or payload.get("check_id")
                or payload.get("bid_id")
                or payload.get("tap_id")
                or payload.get("decision_id")
                or payload.get("rooftop_id")
                or payload.get("credit_application_id")
                or payload.get("vehicle_id")
            ),
        )
        db.add(recommendation)
        outcome = "deferred"
        action_taken = "flagged_for_human_review"
    else:
        outcome = "success"
        action_taken = "no_action_required_low_risk"

    if agent_row:
        agent_row.decisions_made += 1
        if outcome == "deferred":
            agent_row.recommendations_pending += 1

    log = DecisionLog(
        tenant_id=tenant_id,
        agent_key=AGENT_KEY,
        trigger_event_type=event_type,
        trigger_context=payload,
        reasoning=reasoning,
        tools_called=tools_called,
        action_taken=action_taken,
        outcome=outcome,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    if recommendation:
        await db.refresh(recommendation)
        log.recommendation_id = recommendation.id
        await db.commit()
        await db.refresh(log)

    return ComplianceEvaluation(decision_log=log, recommendation=recommendation)
