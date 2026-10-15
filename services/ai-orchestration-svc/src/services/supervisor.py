"""
Supervisor — receives every routed platform event and dispatches it to
the specialist agent(s) responsible for that event type. This is the
literal routing table; adding a new agent means adding a line here, not
standing up a new service.

Each routing entry is (agent_key, handler) rather than a bare handler.
Before invoking a handler, the supervisor checks that agent's row in the
Agent table: PAUSED or DISABLED means the handler is skipped entirely,
not merely "informed but ignored." Without this check, AI-001's
pause/resume control (and SAP-006's kill switches, which just flip the
same status field from a different screen) would be cosmetic — pausing
an agent has to actually stop it from acting, not just stop it from
being reported as active.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Agent, AgentStatus
from src.services import (
    compliance_audit_agent,
    concierge_agent,
    fleet_intelligence_agent,
    member_experience_agent,
    portfolio_intelligence_agent,
    pricing_intelligence_agent,
    revenue_agent,
    talent_agent,
)

AgentHandler = Callable[[AsyncSession, str, str, dict], Awaitable[Any]]


@dataclass
class SkippedEvaluation:
    agent_key: str
    reason: str


# Canonical event -> (agent_key, handler) routing table. Only
# compliance_audit_agent is wired for real today (Super Admin Panel
# pilot); the remaining rows are the documented target state so the
# table is complete and each future agent slots into an already-known
# contract.
EVENT_ROUTING: dict[str, list[tuple[str, AgentHandler]]] = {
    "executive.kpi_alert.triggered": [(portfolio_intelligence_agent.AGENT_KEY, portfolio_intelligence_agent.handle_kpi_alert)],
    "management.kpi_alert.triggered": [(portfolio_intelligence_agent.AGENT_KEY, portfolio_intelligence_agent.handle_kpi_alert)],
    "real_estate.kpi_alert.triggered": [(portfolio_intelligence_agent.AGENT_KEY, portfolio_intelligence_agent.handle_kpi_alert)],
    "capital_finance.kpi_alert.triggered": [(portfolio_intelligence_agent.AGENT_KEY, portfolio_intelligence_agent.handle_kpi_alert)],
    "portfolio.rollup.scheduled": [(portfolio_intelligence_agent.AGENT_KEY, portfolio_intelligence_agent.handle_rollup_scheduled)],
    "role.assigned": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "role.revoked": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "permission.granted": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "permission.revoked": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "abac_policy.activated": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "panel.granted": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "access.break_glass_activated": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "access.impersonation_started": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "feature_flag.kill_switch_toggled": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "content.published": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "role.privilege_drift_detected": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "content_draft.approved_with_violations": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "voice_sms.quiet_hours_override": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "push.frequency_cap_overridden": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "asset.expired_usage_override": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "incident.total_loss_declared": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "vehicle.tamper_detected": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "floorplan.covenant_breached": [
        (compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)
    ],
    "subscription.created": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_event)],
    "travel.activated": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_event)],
    "vehicle.idle.48h": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_event)],
    "gu.fraud_suspected": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "subscription.above_class_request_recorded": [(member_experience_agent.AGENT_KEY, member_experience_agent.handle_above_class_request_recorded)],
    "deposit.capture_requested": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "rental.booked": [(pricing_intelligence_agent.AGENT_KEY, pricing_intelligence_agent.handle_event)],
    "adverse_action.notice_overdue": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "red_flag.check_overridden": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "franchise.facility_non_compliant": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "credit.ofac_match_detected": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "vhr.title_issue_disclosure_overridden": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "auction.overbid_book_override": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "card.geographic_anomaly_detected": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "hub.touchpoint_exception_flagged": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_touchpoint_exception)],
    "flt.safety_score_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_safety_score_requested)],
    "flt.intake_readiness_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_intake_readiness_requested)],
    "flt.telemetry_anomaly_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_telemetry_anomaly_requested)],
    "flt.maintenance_triage_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_maintenance_triage_requested)],
    "flt.incident_severity_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_incident_severity_requested)],
    "flt.valuation_anomaly_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_valuation_anomaly_requested)],
    "flt.disposition_channel_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_disposition_channel_requested)],
    "flt.covenant_risk_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_covenant_risk_requested)],
    "flt.fuel_anomaly_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_fuel_anomaly_requested)],
    "flt.battery_degradation_trend_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_battery_degradation_trend_requested)],
    "flt.fleet_optimization_requested": [(fleet_intelligence_agent.AGENT_KEY, fleet_intelligence_agent.handle_fleet_optimization_requested)],
    "crm.source_record_created": [(revenue_agent.AGENT_KEY, revenue_agent.handle_source_record_created)],
    "lead.captured": [(revenue_agent.AGENT_KEY, revenue_agent.handle_lead_captured)],
    "crm.message_received": [(concierge_agent.AGENT_KEY, concierge_agent.handle_message_received)],
    "crm.person_profile_created": [(revenue_agent.AGENT_KEY, revenue_agent.handle_person_profile_created)],
    "org.case_created": [(revenue_agent.AGENT_KEY, revenue_agent.handle_case_created)],
    "org.decisioning_requested": [(revenue_agent.AGENT_KEY, revenue_agent.handle_decisioning_requested)],
    "org.funding_match_requested": [(revenue_agent.AGENT_KEY, revenue_agent.handle_funding_match_requested)],
    "org.decision_finalized": [(compliance_audit_agent.AGENT_KEY, compliance_audit_agent.evaluate_event)],
    "hrm.employee_created": [(talent_agent.AGENT_KEY_ONBOARDING, talent_agent.handle_employee_created)],
    "hrm.offboarding_initiated": [(talent_agent.AGENT_KEY_ONBOARDING, talent_agent.handle_offboarding_initiated)],
    "hrm.benefits_recommendation_requested": [(talent_agent.AGENT_KEY_BENEFITS, talent_agent.handle_benefits_recommendation_requested)],
    "hrm.application_submitted": [(talent_agent.AGENT_KEY_RECRUITING, talent_agent.handle_application_submitted)],
    "hrm.application_rejected": [(talent_agent.AGENT_KEY_RECRUITING, talent_agent.handle_application_rejected)],
    "hrm.feedback_draft_requested": [(talent_agent.AGENT_KEY_PERFORMANCE, talent_agent.handle_feedback_draft_requested)],
    "hrm.nine_box_evaluation_requested": [(talent_agent.AGENT_KEY_PERFORMANCE, talent_agent.handle_nine_box_evaluation_requested)],
    "hrm.attrition_risk_requested": [(talent_agent.AGENT_KEY_WORKFORCE_ANALYTICS, talent_agent.handle_attrition_risk_requested)],
    "hrm.course_recommendation_requested": [(talent_agent.AGENT_KEY_ACADEMY, talent_agent.handle_course_recommendation_requested)],
    "hrm.policy_question_asked": [(talent_agent.AGENT_KEY_CASE_MANAGEMENT, talent_agent.handle_policy_question_asked)],
    "hrm.note_update_requested": [(talent_agent.AGENT_KEY_COMMUNICATION, talent_agent.handle_note_update_requested)],
    "hrm.case_resolved": [(talent_agent.AGENT_KEY_COMMUNICATION, talent_agent.handle_case_resolved)],
    "hrm.email_draft_requested": [(talent_agent.AGENT_KEY_COMMUNICATION, talent_agent.handle_email_draft_requested)],
    "hrm.course_draft_requested": [(talent_agent.AGENT_KEY_ACADEMY, talent_agent.handle_course_draft_requested)],
}


async def _agent_status(db: AsyncSession, tenant_id: str, agent_key: str) -> AgentStatus:
    """No Agent row yet (never explicitly registered via AI-001) defaults
    to ACTIVE — an agent shouldn't need a registration ceremony before its
    first real event just to avoid being treated as paused."""
    result = await db.execute(
        select(Agent).where(Agent.key == agent_key, Agent.tenant_id == tenant_id)
    )
    agent_row = result.scalar_one_or_none()
    return agent_row.status if agent_row else AgentStatus.ACTIVE


async def route_event(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> list:
    routes = EVENT_ROUTING.get(event_type, [])
    results = []
    for agent_key, handler in routes:
        status = await _agent_status(db, tenant_id, agent_key)
        if status != AgentStatus.ACTIVE:
            results.append(SkippedEvaluation(agent_key=agent_key, reason=f"agent status is {status.value}"))
            continue
        results.append(await handler(db, tenant_id, event_type, payload))
    return results
