from src.models import Agent, AgentStatus
from src.services import compliance_audit_agent
from src.services.supervisor import EVENT_ROUTING, SkippedEvaluation, route_event

TENANT = "voyqi-holdings"


def test_routing_table_covers_all_rbac_events():
    for event_type in [
        "role.assigned", "role.revoked", "permission.granted", "permission.revoked",
        "abac_policy.activated", "panel.granted",
        "access.break_glass_activated", "access.impersonation_started",
        "feature_flag.kill_switch_toggled", "content.published",
        "role.privilege_drift_detected", "content_draft.approved_with_violations",
        "voice_sms.quiet_hours_override", "push.frequency_cap_overridden",
        "asset.expired_usage_override",
        "incident.total_loss_declared", "vehicle.tamper_detected", "floorplan.covenant_breached",
        "gu.fraud_suspected",
        "rental.booked",
        "adverse_action.notice_overdue", "red_flag.check_overridden", "franchise.facility_non_compliant",
        "credit.ofac_match_detected",
        "vhr.title_issue_disclosure_overridden",
        "auction.overbid_book_override",
        "card.geographic_anomaly_detected",
        "hub.touchpoint_exception_flagged",
        "flt.safety_score_requested",
        "flt.intake_readiness_requested",
        "flt.telemetry_anomaly_requested",
        "flt.maintenance_triage_requested",
        "flt.incident_severity_requested",
        "flt.valuation_anomaly_requested",
        "flt.disposition_channel_requested",
        "flt.covenant_risk_requested",
        "flt.fuel_anomaly_requested",
        "flt.battery_degradation_trend_requested",
        "flt.fleet_optimization_requested",
        "crm.source_record_created",
        "lead.captured",
        "crm.message_received",
        "crm.person_profile_created",
        "org.case_created",
        "org.decisioning_requested",
        "org.funding_match_requested",
        "org.decision_finalized",
        "hrm.employee_created",
        "hrm.offboarding_initiated",
        "hrm.benefits_recommendation_requested",
        "hrm.application_submitted",
        "hrm.application_rejected",
        "hrm.feedback_draft_requested",
        "hrm.nine_box_evaluation_requested",
        "hrm.attrition_risk_requested",
        "hrm.course_recommendation_requested",
        "hrm.policy_question_asked",
        "hrm.note_update_requested",
        "hrm.case_resolved",
        "hrm.email_draft_requested",
        "hrm.course_draft_requested",
        "subscription.created", "travel.activated", "vehicle.idle.48h",
    ]:
        assert event_type in EVENT_ROUTING
        assert len(EVENT_ROUTING[event_type]) >= 1


async def test_route_event_dispatches_to_compliance_agent(db_session):
    payload = {
        "override_id": "ov-5",
        "user_id": "user-5",
        "permission_key": "marketing.campaign.view",
        "effect": "allow",
        "is_sensitive": False,
        "granted_by": "garth",
        "ai_initiated": False,
    }
    results = await route_event(db_session, TENANT, "permission.granted", payload)
    assert len(results) == 1
    assert results[0].decision_log.trigger_event_type == "permission.granted"


async def test_unrouted_event_type_returns_empty(db_session):
    results = await route_event(db_session, TENANT, "some.unknown.event", {})
    assert results == []


async def test_unregistered_agent_defaults_to_active(db_session):
    # No Agent row exists for compliance_audit_agent in this tenant yet —
    # routing should still proceed rather than treat it as paused.
    payload = {
        "override_id": "ov-6", "user_id": "user-6", "permission_key": "marketing.campaign.view",
        "effect": "allow", "is_sensitive": False, "granted_by": "garth", "ai_initiated": False,
    }
    results = await route_event(db_session, TENANT, "permission.granted", payload)
    assert len(results) == 1
    assert not isinstance(results[0], SkippedEvaluation)


async def test_paused_agent_is_skipped_not_evaluated(db_session):
    agent_row = Agent(
        tenant_id=TENANT, key=compliance_audit_agent.AGENT_KEY,
        display_name="Compliance/Audit Agent", status=AgentStatus.PAUSED,
    )
    db_session.add(agent_row)
    await db_session.commit()

    payload = {
        "override_id": "ov-7", "user_id": "user-7", "permission_key": "rbac.role.grant_admin",
        "effect": "allow", "is_sensitive": True, "granted_by": "garth", "ai_initiated": False,
    }
    results = await route_event(db_session, TENANT, "permission.granted", payload)
    assert len(results) == 1
    assert isinstance(results[0], SkippedEvaluation)
    assert results[0].agent_key == compliance_audit_agent.AGENT_KEY
    assert "paused" in results[0].reason


async def test_disabled_agent_is_skipped(db_session):
    agent_row = Agent(
        tenant_id=TENANT, key=compliance_audit_agent.AGENT_KEY,
        display_name="Compliance/Audit Agent", status=AgentStatus.DISABLED,
    )
    db_session.add(agent_row)
    await db_session.commit()

    results = await route_event(db_session, TENANT, "role.assigned", {"user_id": "user-8"})
    assert isinstance(results[0], SkippedEvaluation)


async def test_reactivated_agent_resumes_evaluating(db_session):
    agent_row = Agent(
        tenant_id=TENANT, key=compliance_audit_agent.AGENT_KEY,
        display_name="Compliance/Audit Agent", status=AgentStatus.PAUSED,
    )
    db_session.add(agent_row)
    await db_session.commit()
    await db_session.refresh(agent_row)

    agent_row.status = AgentStatus.ACTIVE
    await db_session.commit()

    payload = {
        "override_id": "ov-8", "user_id": "user-9", "permission_key": "marketing.campaign.view",
        "effect": "allow", "is_sensitive": False, "granted_by": "garth", "ai_initiated": False,
    }
    results = await route_event(db_session, TENANT, "permission.granted", payload)
    assert not isinstance(results[0], SkippedEvaluation)
