"""The three-way autonomy branch every executing agent action goes
through (docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §1.2):

  tier 1 -> execute now, log with tier=1
  tier 2 -> queue a PENDING AIRecommendation; execute only on approval
  tier 3 -> same queue, but approval also requires a named accountable_role

Executing actions are referenced by a registry key rather than a
closure, so a queued recommendation can be executed later — by
api/recommendations.approve_recommendation — from what is stored in
its proposed_action.
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.tiering import get_tier
from src.models import AIRecommendation, RecommendationStatus
from src.services import dms_tools, hrm_tools, subscription_tools


def _call(module, name: str, *arg_names: str) -> Callable[[str, dict], Any]:
    """Resolves the tool function at call time, not import time, so a
    queued action runs whatever the module currently exposes (and tests
    can substitute tools the usual way)."""
    return lambda tenant_id, args: getattr(module, name)(tenant_id, *(args[a] for a in arg_names))


# registry key -> (tenant_id, args) -> tool result
EXECUTORS: dict[str, Callable[[str, dict], Any]] = {
    "hrm.accept_benefits_recommendation": _call(hrm_tools, "accept_benefits_recommendation", "recommendation_id"),
    "hrm.send_nurture_draft": _call(hrm_tools, "send_nurture_draft", "draft_id"),
    "hrm.finalize_feedback_draft": _call(hrm_tools, "finalize_feedback_draft", "draft_id"),
    "hrm.confirm_nine_box_placement": _call(hrm_tools, "confirm_nine_box_placement", "placement_id"),
    "hrm.accept_course_recommendation": _call(hrm_tools, "accept_course_recommendation", "recommendation_id"),
    "hrm.send_case_reply": _call(hrm_tools, "send_case_reply", "draft_id"),
    "hrm.send_email": _call(hrm_tools, "send_email", "email_id"),
    "hrm.publish_course_draft": _call(hrm_tools, "publish_course_draft", "draft_id"),
    "hrm.add_employee_note": _call(hrm_tools, "add_employee_note", "employee_id", "body"),
    "hrm.append_employee_note": _call(hrm_tools, "append_employee_note", "note_id", "additional_text"),
    "subscription.award_retention_gu_bonus": _call(subscription_tools, "award_retention_gu_bonus", "member_profile_id", "amount"),
    "dms.evaluate_license_renewals": _call(dms_tools, "evaluate_license_renewals"),
    "dms.evaluate_aging_sweep": _call(dms_tools, "evaluate_aging_sweep"),
    "dms.desk_and_approve_deal": _call(dms_tools, "desk_and_approve_deal", "deal_id"),
    "dms.dispatch_repair_order": _call(dms_tools, "dispatch_repair_order", "rooftop_id", "vin", "customer_name", "description", "warranty"),
    "dms.evaluate_parts_reorder": _call(dms_tools, "evaluate_parts_reorder"),
    "dms.evaluate_loaner_overdue": _call(dms_tools, "evaluate_loaner_overdue"),
    "dms.evaluate_franchise_remediation": _call(dms_tools, "evaluate_franchise_remediation"),
    "dms.pull_bureau_and_route": _call(dms_tools, "pull_bureau_and_route", "app_id", "bureau", "fico", "ofac_match"),
    "dms.evaluate_dealer_performance": _call(dms_tools, "evaluate_dealer_performance"),
}


@dataclass
class GateResult:
    tier: int
    executed: bool
    result: Any = None
    recommendation: AIRecommendation | None = None


async def gate(
    db: AsyncSession,
    *,
    tenant_id: str,
    agent_key: str,
    policy_agent: str,
    action_type: str,
    executor: str,
    args: dict,
    reasoning: str,
    triggering_event_type: str,
    domain: str,
    context: dict | None = None,
    external_record_id: str | None = None,
) -> GateResult:
    """Executes the action now if the tier policy allows it, otherwise
    queues it for human approval. Does not commit — the caller owns the
    transaction, alongside its own DecisionLog."""
    if executor not in EXECUTORS:
        raise KeyError(f"unknown executor {executor!r}")
    tier = get_tier(policy_agent, action_type, context or {})
    if tier == 1:
        return GateResult(tier=1, executed=True, result=EXECUTORS[executor](tenant_id, args))

    recommendation = AIRecommendation(
        tenant_id=tenant_id,
        agent_key=agent_key,
        domain=domain,
        action_type=action_type,
        proposed_action={"executor": executor, "args": args, "policy": f"{policy_agent}.{action_type}"},
        reasoning=reasoning,
        risk_score=0.0,
        drivers=[f"tier {tier}: {policy_agent}.{action_type} requires human approval before it executes"],
        external_record_id=external_record_id,
        status=RecommendationStatus.PENDING,
        triggering_event_type=triggering_event_type,
        tier=tier,
        requires_accountable_role=tier == 3,
    )
    db.add(recommendation)
    await db.flush()
    return GateResult(tier=tier, executed=False, recommendation=recommendation)


def execute_approved(tenant_id: str, proposed_action: dict) -> Any:
    """Runs a gated action once a human approves its recommendation.
    Returns None for recommendations that weren't created by gate()."""
    executor = (proposed_action or {}).get("executor")
    if executor is None:
        return None
    return EXECUTORS[executor](tenant_id, proposed_action.get("args", {}))
