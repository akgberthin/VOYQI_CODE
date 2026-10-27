"""
Autonomy tier lookup — the single source of truth for whether an agent
action executes immediately, waits for approval, or requires a named
accountable human before it can be approved at all.

docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §1.1: "This file [the
policy YAML] is the single source of truth for autonomy decisions — do
not hardcode tier logic in application code." Every call site passes
its (agent, action_type, context) through get_tier() rather than
branching on a locally-defined threshold.
"""
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.core.config import get_settings


@lru_cache(maxsize=1)
def _load_policy() -> dict:
    settings = get_settings()
    path = Path(settings.tier_policy_path)
    return yaml.safe_load(path.read_text())


def _evaluate_threshold(threshold: str, context: dict[str, Any]) -> bool | None:
    """Threshold expressions in the policy are simple comparisons like
    "cost_delta_usd > 150" against a single context field. Returns
    True/False when the comparison can actually be evaluated, and None
    when it can't (unrecognized expression, missing or non-numeric
    field) — ambiguity is never read as "not met"."""
    for op, fn in ((">=", lambda a, b: a >= b), ("<=", lambda a, b: a <= b),
                   (">", lambda a, b: a > b), ("<", lambda a, b: a < b),
                   ("==", lambda a, b: a == b)):
        if op in threshold:
            field, _, raw_value = threshold.partition(op)
            field = field.strip()
            if field not in context:
                return None
            try:
                return fn(float(context[field]), float(raw_value.strip()))
            except (TypeError, ValueError):
                return None
    return None


def get_tier(agent: str, action_type: str, context: dict[str, Any] | None = None) -> int:
    """
    Returns 1, 2, or 3 for the given agent/action_type pair. Falls back
    to policy['default_tier'] (2) if the pair isn't listed at all —
    silence must never mean autonomous.

    A threshold states when the configured tier applies (e.g. tier 2
    when "offer_value_usd > 500"). Only a threshold that is evaluated
    and clearly not met lowers the tier by one; an ambiguous one keeps
    the configured tier. A threshold never raises a tier — tier 3 is a
    regulatory category set explicitly in the policy, never reached by
    a number — and a tier-3 action is never lowered.
    """
    policy = _load_policy()
    action_cfg = policy.get("agents", {}).get(agent, {}).get("actions", {}).get(action_type)
    if action_cfg is None:
        return policy["default_tier"]

    tier = action_cfg["tier"]
    threshold = action_cfg.get("threshold")
    if tier != 3:  # tier 3 is never overridden, whatever is stored (AI-005)
        override = _override_for(agent, action_type)
        if override is not None:
            tier, threshold = override
    if tier == 3 or not threshold:
        return tier
    if _evaluate_threshold(threshold, context or {}) is False:
        return max(tier - 1, 1)
    return tier


# (tenant_id, policy_agent, action_type) -> (tier, threshold); loaded by
# core/tier_overrides.refresh and kept current on every change.
_overrides: dict[tuple[str, str, str], tuple[int, str | None]] = {}


def _override_for(agent: str, action_type: str) -> tuple[int, str | None] | None:
    from src.core.prompts import current_tenant  # the request/event's tenant

    tenant = current_tenant.get()
    return _overrides.get((tenant, agent, action_type)) if tenant else None


def base_action(agent: str, action_type: str) -> dict | None:
    """The tier-policy.yaml entry, ignoring tenant overrides."""
    return _load_policy().get("agents", {}).get(agent, {}).get("actions", {}).get(action_type)


def approval_required(tier: int) -> bool:
    return tier >= 2


def requires_accountable_role(tier: int) -> bool:
    return tier >= 3


# Which tier-policy action each agent decision represents, so every
# DecisionLog row carries its tier and approval_required — the AI-004
# graduation data (tiering instructions §1.5). Keyed by
# (agent_key, trigger_event_type); "*" matches any trigger for that agent.
# Call sites that branch through core/action_gate set the tier
# themselves; this only fills rows that didn't.
DECISION_ACTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("compliance_audit_agent", "*"): ("compliance-risk-intelligence", "audit.flag_anomaly"),
    ("concierge_agent", "crm.message_received"): ("relationship-intelligence", "inbox.assess_draft_escalate"),
    ("marketing_content_agent", "content.draft_requested"): ("marketing-content", "content.draft_generate"),
    ("marketing_content_agent", "marketing.compose_requested"): ("marketing-content", "content.draft_generate"),
    ("pricing_intelligence_agent", "rental.booked"): ("pricing-intelligence", "pricing.adjust_within_band"),
    ("pricing_intelligence_agent", "trade_appraisal.valuation_requested"): ("pricing-intelligence", "trade.valuation_apply"),
    ("pricing_intelligence_agent", "retail_pricing.recommendation_requested"): ("pricing-intelligence", "retail.price_apply"),
    ("pricing_intelligence_agent", "max_bid.recommendation_requested"): ("pricing-intelligence", "auction.max_bid_recommend"),
    ("revenue_agent", "crm.source_record_created"): ("relationship-intelligence", "identity.match_candidate_propose"),
    ("revenue_agent", "crm.person_profile_created"): ("relationship-intelligence", "identity.duplicate_candidate_propose"),
    ("revenue_agent", "lead.captured"): ("sales-originations-intelligence", "lead.score_and_route"),
    ("revenue_agent", "org.case_created"): ("sales-originations-intelligence", "case.prequalify"),
    ("revenue_agent", "org.decisioning_requested"): ("sales-originations-intelligence", "case.risk_score"),
    ("revenue_agent", "org.funding_match_requested"): ("sales-originations-intelligence", "lender.match_recommend"),
    ("member_experience_agent", "churn_risk.evaluation_requested"): ("retention", "churn.retention_bonus_award"),
    ("member_experience_agent", "subscription.cancellation_initiated"): ("retention", "winback.evaluate"),
    ("member_experience_agent", "subscription.above_class_request_recorded"): ("member-experience", "upgrade.recommendation_surface"),
    ("fleet_intelligence_agent", "flt.battery_degradation_trend_requested"): ("fleet-intelligence", "battery.degradation_forecast"),
    ("fleet_intelligence_agent", "flt.covenant_risk_requested"): ("fleet-intelligence", "financing.covenant_early_warning"),
    ("fleet_intelligence_agent", "flt.disposition_channel_requested"): ("fleet-intelligence", "remarketing.disposition_recommend"),
    ("fleet_intelligence_agent", "flt.fleet_optimization_requested"): ("fleet-intelligence", "fleet.optimization_assess"),
    ("fleet_intelligence_agent", "flt.fuel_anomaly_requested"): ("fleet-intelligence", "fuel.anomaly_flag"),
    ("fleet_intelligence_agent", "flt.incident_severity_requested"): ("fleet-intelligence", "incident.severity_triage"),
    ("fleet_intelligence_agent", "flt.intake_readiness_requested"): ("fleet-intelligence", "intake.readiness_assess"),
    ("fleet_intelligence_agent", "flt.maintenance_triage_requested"): ("fleet-intelligence", "maintenance.predictive_alert"),
    ("fleet_intelligence_agent", "flt.safety_score_requested"): ("fleet-intelligence", "safety.score_assess"),
    ("fleet_intelligence_agent", "flt.telemetry_anomaly_requested"): ("fleet-intelligence", "telemetry.anomaly_assess"),
    ("fleet_intelligence_agent", "flt.valuation_anomaly_requested"): ("fleet-intelligence", "valuation.anomaly_flag"),
    ("fleet_intelligence_agent", "*"): ("fleet-intelligence", "staging.rebalance_within_threshold"),
    ("dealer_intelligence_agent", "lending.bureau_pull_requested"): ("sales-originations-intelligence", "credit.decision"),
    ("dealer_intelligence_agent", "deal.desk_requested"): ("sales-originations-intelligence", "deal.structure_recommend"),
    ("dealer_intelligence_agent", "rooftop.renewal_sweep_requested"): ("dealer-operations", "rooftop.license_renewal_file"),
    ("dealer_intelligence_agent", "inventory.aging_sweep_requested"): ("dealer-operations", "inventory.aging_markdown_apply"),
    ("dealer_intelligence_agent", "service.repair_order_opened"): ("dealer-operations", "service.repair_order_dispatch"),
    ("dealer_intelligence_agent", "parts.reorder_sweep_requested"): ("dealer-operations", "parts.vendor_order_place"),
    ("dealer_intelligence_agent", "loaner.overdue_sweep_requested"): ("dealer-operations", "loaner.overdue_flag"),
    ("dealer_intelligence_agent", "franchise.remediation_sweep_requested"): ("dealer-operations", "franchise.remediation_task_open"),
    ("dealer_intelligence_agent", "performance.evaluation_requested"): ("dealer-operations", "performance.intervention_markdown_apply"),
    ("portfolio_intelligence_agent", "portfolio.rollup.scheduled"): ("portfolio-intelligence", "rollup.generate_report"),
    ("portfolio_intelligence_agent", "portfolio.report_requested"): ("portfolio-intelligence", "rollup.generate_report"),
    ("portfolio_intelligence_agent", "*"): ("portfolio-intelligence", "kpi_alert.assess"),
    ("onboarding_agent", "*"): ("talent-workforce-intelligence", "onboarding.checklist_generate"),
    ("benefits_agent", "*"): ("talent-workforce-intelligence", "benefits.recommendation_apply"),
    ("recruiting_agent", "hrm.application_submitted"): ("talent-workforce-intelligence", "candidate.screen_score"),
    ("recruiting_agent", "hrm.application_rejected"): ("talent-workforce-intelligence", "candidate.nurture_send"),
    ("performance_agent", "hrm.feedback_draft_requested"): ("talent-workforce-intelligence", "feedback.finalize"),
    ("performance_agent", "hrm.nine_box_evaluation_requested"): ("talent-workforce-intelligence", "nine_box.placement_confirm"),
    ("workforce_analytics_agent", "*"): ("talent-workforce-intelligence", "attrition.risk_score"),
    ("academy_agent", "hrm.course_draft_requested"): ("talent-workforce-intelligence", "course.publish"),
    ("academy_agent", "hrm.course_recommendation_requested"): ("talent-workforce-intelligence", "course.recommendation_assign"),
    ("case_management_agent", "*"): ("talent-workforce-intelligence", "case.reply_send"),
    ("communication_agent", "hrm.email_draft_requested"): ("talent-workforce-intelligence", "email.send"),
    ("communication_agent", "hrm.note_update_requested"): ("talent-workforce-intelligence", "employee_note.write"),
}


def tier_for_decision(agent_key: str, trigger_event_type: str) -> int | None:
    action = DECISION_ACTIONS.get((agent_key, trigger_event_type)) or DECISION_ACTIONS.get((agent_key, "*"))
    if action is None:
        return None
    return get_tier(*action)
