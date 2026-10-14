from unittest.mock import MagicMock

import pytest

from src.core.anthropic_client import AgentTurnResult, ToolCallResult
from src.models import Agent, RecommendationStatus
from src.services import compliance_audit_agent, rbac_tools

TENANT = "voyqi-holdings"


class _UnconfiguredClient:
    is_configured = False


class _FakeConfiguredClient:
    is_configured = True

    def __init__(self, final_text="Elevated risk: sensitive permission granted by an AI agent."):
        self._final_text = final_text
        self.run_agent_turn = MagicMock(
            return_value=AgentTurnResult(
                final_text=self._final_text,
                tool_calls=[
                    ToolCallResult(
                        tool_name="get_user_rbac_context",
                        tool_input={"user_id": "user-1"},
                        tool_output={"active_roles": [], "active_overrides": []},
                    )
                ],
                stop_reason="end_turn",
            )
        )


async def test_low_risk_permission_grant_auto_clears_without_recommendation(db_session):
    payload = {
        "override_id": "ov-1",
        "user_id": "user-1",
        "permission_key": "marketing.campaign.view",
        "effect": "allow",
        "is_sensitive": False,
        "granted_by": "garth",
        "ai_initiated": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "permission.granted", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is None
    assert evaluation.decision_log.outcome == "success"
    assert evaluation.decision_log.action_taken == "no_action_required_low_risk"
    assert "deterministic fallback" in evaluation.decision_log.reasoning


async def test_sensitive_ai_initiated_grant_creates_pending_recommendation(db_session):
    payload = {
        "override_id": "ov-2",
        "user_id": "user-2",
        "permission_key": "rbac.role.grant_admin",
        "effect": "allow",
        "is_sensitive": True,
        "granted_by": "ai-orchestration-svc",
        "ai_initiated": True,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "permission.granted", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.status == RecommendationStatus.PENDING
    assert evaluation.recommendation.risk_score >= 50
    assert evaluation.decision_log.action_taken == "flagged_for_human_review"


async def test_deny_override_lowers_risk_score(db_session):
    payload = {
        "override_id": "ov-3",
        "user_id": "user-3",
        "permission_key": "billing.refund.approve",
        "effect": "deny",
        "is_sensitive": True,
        "granted_by": "garth",
        "ai_initiated": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "permission.granted", payload, client=_UnconfiguredClient()
    )
    # sensitive (+60) but DENY (-20) => 40, below default threshold of 50
    assert evaluation.recommendation is None
    assert evaluation.decision_log.outcome == "success"


async def test_ai_initiated_role_assignment_flagged(db_session):
    payload = {"assignment_id": "ra-1", "user_id": "user-4", "role_id": "role-1", "ai_initiated": True}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "role.assigned", payload, client=_UnconfiguredClient()
    )
    # 25 risk points alone is below threshold — should NOT be flagged on its own
    assert evaluation.recommendation is None
    assert evaluation.decision_log.trigger_event_type == "role.assigned"


async def test_custom_agent_threshold_is_respected(db_session):
    agent_row = Agent(tenant_id=TENANT, key=compliance_audit_agent.AGENT_KEY, display_name="Compliance/Audit Agent", human_approval_threshold=20)
    db_session.add(agent_row)
    await db_session.commit()

    payload = {"assignment_id": "ra-2", "user_id": "user-5", "role_id": "role-2", "ai_initiated": True}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "role.assigned", payload, client=_UnconfiguredClient()
    )
    # 25 risk points now exceeds the lowered threshold of 20
    assert evaluation.recommendation is not None
    await db_session.refresh(agent_row)
    assert agent_row.decisions_made == 1
    assert agent_row.recommendations_pending == 1


async def test_live_claude_path_uses_agent_reasoning_and_tool_calls(db_session, monkeypatch):
    monkeypatch.setattr(
        rbac_tools, "build_tool_dispatch", lambda tenant_id: {"get_user_rbac_context": lambda inp: {}}
    )
    payload = {
        "override_id": "ov-9",
        "user_id": "user-9",
        "permission_key": "capital.cap_table.edit",
        "effect": "allow",
        "is_sensitive": True,
        "granted_by": "garth",
        "ai_initiated": False,
    }
    fake_client = _FakeConfiguredClient()
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "permission.granted", payload, client=fake_client
    )
    fake_client.run_agent_turn.assert_called_once()
    assert "Elevated risk" in evaluation.decision_log.reasoning
    assert evaluation.decision_log.tools_called == [
        {"tool": "get_user_rbac_context", "input": {"user_id": "user-1"}}
    ]


async def test_self_granted_permission_flagged_regardless_of_sensitivity(db_session, monkeypatch):
    monkeypatch.setattr(rbac_tools, "get_user_rbac_context", lambda tenant_id, user_id: {"active_roles": [], "active_overrides": []})
    payload = {
        "override_id": "ov-20",
        "user_id": "user-20",
        "permission_key": "marketing.campaign.view",  # not on the sensitive list
        "effect": "allow",
        "is_sensitive": False,
        "granted_by": "user-20",  # granted to themselves
        "ai_initiated": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "permission.granted", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert any("self-grant" in d for d in evaluation.recommendation.drivers)


async def test_self_assigned_role_flagged(db_session, monkeypatch):
    monkeypatch.setattr(rbac_tools, "get_user_rbac_context", lambda tenant_id, user_id: {"active_roles": [], "active_overrides": []})
    payload = {"assignment_id": "ra-30", "user_id": "user-30", "role_id": "role-30", "assigned_by": "user-30", "ai_initiated": False}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "role.assigned", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert any("self-grant" in d for d in evaluation.recommendation.drivers)


async def test_rapid_privilege_accumulation_flagged(db_session, monkeypatch):
    heavy_context = {
        "active_roles": [{"role_key": f"role_{i}"} for i in range(4)],
        "active_overrides": [{"permission_key": "x"}],
    }
    monkeypatch.setattr(rbac_tools, "get_user_rbac_context", lambda tenant_id, user_id: heavy_context)
    payload = {
        "override_id": "ov-40",
        "user_id": "user-40",
        "permission_key": "marketing.campaign.view",
        "effect": "allow",
        "is_sensitive": False,
        "granted_by": "garth",
        "ai_initiated": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "permission.granted", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert any("rapid privilege accumulation" in d for d in evaluation.recommendation.drivers)


async def test_context_lookup_failure_does_not_break_evaluation(db_session, monkeypatch):
    def _boom(tenant_id, user_id):
        raise ConnectionError("rbac-svc unreachable")

    monkeypatch.setattr(rbac_tools, "get_user_rbac_context", _boom)
    payload = {
        "override_id": "ov-41",
        "user_id": "user-41",
        "permission_key": "marketing.campaign.view",
        "effect": "allow",
        "is_sensitive": False,
        "granted_by": "garth",
        "ai_initiated": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "permission.granted", payload, client=_UnconfiguredClient()
    )
    assert evaluation.decision_log.outcome == "success"
    assert evaluation.recommendation is None


async def test_sensitive_abac_policy_flagged(db_session):
    payload = {
        "policy_id": "pol-1",
        "permission_key": "rbac.role.grant_admin",
        "effect": "allow",
        "is_sensitive": True,
        "had_conflicts_forced": False,
        "created_by": "garth",
        "ai_initiated": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "abac_policy.activated", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.action_type == "review_abac_policy"
    assert evaluation.recommendation.external_record_id == "pol-1"


async def test_force_activated_conflicting_policy_flagged(db_session):
    payload = {
        "policy_id": "pol-2",
        "permission_key": "marketing.campaign.view",
        "effect": "allow",
        "is_sensitive": False,
        "had_conflicts_forced": True,
        "created_by": "garth",
        "ai_initiated": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "abac_policy.activated", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert any("force-activated" in d for d in evaluation.recommendation.drivers)


async def test_low_risk_abac_policy_clears(db_session):
    payload = {
        "policy_id": "pol-3",
        "permission_key": "marketing.campaign.view",
        "effect": "deny",
        "is_sensitive": False,
        "had_conflicts_forced": False,
        "created_by": "garth",
        "ai_initiated": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "abac_policy.activated", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is None


async def test_sensitive_panel_grant_flagged(db_session):
    payload = {
        "grant_id": "grant-1",
        "panel_key": "ai_orchestration",
        "role_key": "ops_lead",
        "user_id": None,
        "is_sensitive": True,
        "granted_by": "garth",
        "ai_initiated": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "panel.granted", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.action_type == "review_panel_grant"
    assert evaluation.recommendation.external_record_id == "grant-1"


async def test_non_sensitive_panel_grant_clears(db_session):
    payload = {
        "grant_id": "grant-2",
        "panel_key": "marketing_admin",
        "role_key": "marketing_manager",
        "user_id": None,
        "is_sensitive": False,
        "granted_by": "garth",
        "ai_initiated": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "panel.granted", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is None


async def test_break_glass_activation_always_flagged(db_session, monkeypatch):
    monkeypatch.setattr(
        rbac_tools, "get_user_rbac_context", lambda tenant_id, user_id: {"active_roles": [], "active_overrides": []}
    )
    payload = {
        "session_id": "bg-1", "user_id": "user-60", "activated_by": "garth",
        "duration_minutes": 20, "off_hours": False,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "access.break_glass_activated", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "identity"
    assert evaluation.recommendation.action_type == "review_break_glass_session"
    assert evaluation.recommendation.external_record_id == "bg-1"


async def test_off_hours_break_glass_scores_higher(db_session, monkeypatch):
    monkeypatch.setattr(
        rbac_tools, "get_user_rbac_context", lambda tenant_id, user_id: {"active_roles": [], "active_overrides": []}
    )
    payload_business_hours = {
        "session_id": "bg-2", "user_id": "user-61", "activated_by": "garth",
        "duration_minutes": 20, "off_hours": False,
    }
    payload_off_hours = {**payload_business_hours, "session_id": "bg-3", "off_hours": True}

    eval_business = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "access.break_glass_activated", payload_business_hours, client=_UnconfiguredClient()
    )
    eval_off_hours = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "access.break_glass_activated", payload_off_hours, client=_UnconfiguredClient()
    )
    assert eval_off_hours.recommendation.risk_score > eval_business.recommendation.risk_score


async def test_impersonation_without_recording_flagged_higher(db_session, monkeypatch):
    monkeypatch.setattr(
        rbac_tools, "get_user_rbac_context", lambda tenant_id, user_id: {"active_roles": [], "active_overrides": []}
    )
    payload_with_recording = {
        "session_id": "imp-1", "user_id": "member-1", "actor_user_id": "support-1",
        "duration_minutes": 15, "off_hours": False, "has_session_recording": True,
    }
    payload_without_recording = {**payload_with_recording, "session_id": "imp-2", "has_session_recording": False}

    eval_with = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "access.impersonation_started", payload_with_recording, client=_UnconfiguredClient()
    )
    eval_without = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "access.impersonation_started", payload_without_recording, client=_UnconfiguredClient()
    )
    assert eval_without.recommendation.risk_score > eval_with.recommendation.risk_score
    assert eval_with.recommendation.action_type == "review_impersonation_session"


async def test_kill_switch_arming_flagged(db_session):
    payload = {
        "flag_id": "flag-1", "target_agent_key": "pricing_intelligence_agent",
        "kill_switch_armed": True, "toggled_by": "garth", "ai_initiated": False,
        "agent_action_applied": True,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "feature_flag.kill_switch_toggled", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "platform_config"
    assert evaluation.recommendation.action_type == "review_kill_switch_toggle"


async def test_kill_switch_disabling_itself_scores_higher(db_session):
    payload_other_agent = {
        "flag_id": "flag-2", "target_agent_key": "pricing_intelligence_agent",
        "kill_switch_armed": True, "toggled_by": "garth", "ai_initiated": False,
        "agent_action_applied": True,
    }
    payload_self = {**payload_other_agent, "flag_id": "flag-3", "target_agent_key": compliance_audit_agent.AGENT_KEY}

    eval_other = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "feature_flag.kill_switch_toggled", payload_other_agent, client=_UnconfiguredClient()
    )
    eval_self = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "feature_flag.kill_switch_toggled", payload_self, client=_UnconfiguredClient()
    )
    assert eval_self.recommendation.risk_score > eval_other.recommendation.risk_score


async def test_kill_switch_disarming_clears(db_session):
    payload = {
        "flag_id": "flag-4", "target_agent_key": "pricing_intelligence_agent",
        "kill_switch_armed": False, "toggled_by": "garth", "ai_initiated": False,
        "agent_action_applied": True,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "feature_flag.kill_switch_toggled", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is None


async def test_legal_disclosure_bypassing_review_flagged(db_session):
    payload = {
        "content_id": "content-1", "content_type": "legal_disclosure",
        "bypassed_review": True, "published_by": "garth",
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "content.published", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "cms"
    assert evaluation.recommendation.action_type == "review_content_publication"


async def test_legal_disclosure_through_normal_review_clears(db_session):
    payload = {
        "content_id": "content-2", "content_type": "legal_disclosure",
        "bypassed_review": False, "published_by": "garth",
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "content.published", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is None


async def test_significant_privilege_drift_flagged(db_session):
    payload = {
        "role_id": "role-1", "template_key": "compliance_baseline",
        "excess_permissions": ["rbac.role.grant_admin", "capital.cap_table.edit", "billing.refund.approve"],
        "excess_permission_count": 3,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "role.privilege_drift_detected", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.action_type == "review_privilege_drift"
    assert evaluation.recommendation.external_record_id == "role-1"


async def test_minor_privilege_drift_does_not_auto_flag(db_session):
    payload = {
        "role_id": "role-2", "template_key": "compliance_baseline",
        "excess_permissions": ["marketing.campaign.view"], "excess_permission_count": 1,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "role.privilege_drift_detected", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is None
    assert evaluation.decision_log.outcome == "success"


async def test_content_draft_guardrail_override_flagged(db_session):
    payload = {
        "draft_id": "draft-1", "campaign_id": "camp-1", "channel": "email",
        "violations": ["contains prohibited claim: 'guaranteed'"], "violation_count": 1,
        "reviewed_by": "marketing-lead",
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "content_draft.approved_with_violations", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "marketing"
    assert evaluation.recommendation.action_type == "review_content_draft_override"
    assert evaluation.recommendation.external_record_id == "draft-1"


async def test_multiple_violations_score_higher(db_session):
    single = {
        "draft_id": "draft-2", "violation_count": 1, "violations": ["a"], "reviewed_by": "x",
    }
    multiple = {
        "draft_id": "draft-3", "violation_count": 3, "violations": ["a", "b", "c"], "reviewed_by": "x",
    }
    eval_single = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "content_draft.approved_with_violations", single, client=_UnconfiguredClient()
    )
    eval_multiple = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "content_draft.approved_with_violations", multiple, client=_UnconfiguredClient()
    )
    assert eval_multiple.recommendation.risk_score > eval_single.recommendation.risk_score


async def test_quiet_hours_override_always_flagged(db_session):
    payload = {
        "send_id": "send-1", "channel": "sms", "recipient_local_hour": 23,
        "override_reason": "customer requested", "created_by": "marketing-lead",
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "voice_sms.quiet_hours_override", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "marketing"
    assert evaluation.recommendation.action_type == "review_quiet_hours_override"
    assert evaluation.recommendation.external_record_id == "send-1"


async def test_frequency_cap_override_alone_does_not_auto_flag(db_session):
    payload = {
        "push_send_id": "push-1", "recipient_id": "member-1",
        "override_reason": "urgent update", "created_by": "marketing-lead",
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "push.frequency_cap_overridden", payload, client=_UnconfiguredClient()
    )
    # A single frequency-cap override is routine business judgment, unlike
    # TCPA quiet-hours which is a legal/compliance concern — deliberately
    # scored below the auto-flag threshold on its own.
    assert evaluation.recommendation is None


async def test_expired_asset_override_flagged(db_session):
    payload = {
        "attachment_id": "attach-1", "asset_id": "asset-1", "content_draft_id": "draft-1",
        "override_reason": "extended license confirmed via email", "attached_by": "marketing-lead",
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "asset.expired_usage_override", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "marketing"
    assert evaluation.recommendation.action_type == "review_expired_asset_usage"
    assert evaluation.recommendation.external_record_id == "attach-1"


async def test_total_loss_declaration_always_flagged(db_session):
    payload = {
        "incident_id": "inc-1", "vehicle_id": "veh-1", "declared_by": "risk-team-1",
        "reason": "repair cost exceeds 80% of book value",
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "incident.total_loss_declared", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "fleet"
    assert evaluation.recommendation.action_type == "review_total_loss_declaration"
    assert evaluation.recommendation.external_record_id == "inc-1"


async def test_tamper_event_always_flagged(db_session):
    payload = {"event_id": "tamper-1", "vehicle_id": "veh-1", "detected_signal": "unexpected cert revocation attempt"}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "vehicle.tamper_detected", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "fleet"
    assert evaluation.recommendation.action_type == "review_tamper_event"
    assert evaluation.recommendation.external_record_id == "tamper-1"


async def test_covenant_breach_always_flagged(db_session):
    payload = {"facility_id": "fac-1", "vehicle_id": "veh-1", "pledged_amount_usd": 60000.0}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "floorplan.covenant_breached", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "fleet"
    assert evaluation.recommendation.action_type == "review_covenant_breach"
    assert evaluation.recommendation.external_record_id == "fac-1"


async def test_gu_fraud_suspected_always_flagged(db_session):
    payload = {"ledger_entry_id": "gu-1", "member_profile_id": "mem-1", "trigger": "referral", "amount": 50}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "gu.fraud_suspected", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "subscription"
    assert evaluation.recommendation.action_type == "review_gu_fraud_flag"
    assert evaluation.recommendation.external_record_id == "gu-1"


async def test_adverse_action_overdue_always_flagged(db_session):
    payload = {"notice_id": "notice-1", "deal_jacket_id": "deal-1", "reason": "denied for insufficient income", "deadline": "2026-01-01T00:00:00+00:00"}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "adverse_action.notice_overdue", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "dms"
    assert evaluation.recommendation.action_type == "review_adverse_action_overdue"
    assert evaluation.recommendation.external_record_id == "notice-1"


async def test_red_flag_override_always_flagged(db_session):
    payload = {"check_id": "check-1", "deal_jacket_id": "deal-1", "flags_detected": ["address mismatch"], "overridden_by": "fi-manager-1", "override_reason": "verified in person"}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "red_flag.check_overridden", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "dms"
    assert evaluation.recommendation.action_type == "review_red_flag_override"
    assert evaluation.recommendation.external_record_id == "check-1"


async def test_franchise_non_compliance_always_flagged(db_session):
    payload = {"rooftop_id": "rooftop-1", "notes": "signage program lapsed"}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "franchise.facility_non_compliant", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "dms"
    assert evaluation.recommendation.action_type == "review_franchise_non_compliance"
    assert evaluation.recommendation.external_record_id == "rooftop-1"


async def test_ofac_match_always_flagged_at_highest_severity(db_session):
    payload = {"credit_application_id": "app-1", "bureau": "experian"}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "credit.ofac_match_detected", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "dms"
    assert evaluation.recommendation.action_type == "review_ofac_match"
    assert evaluation.recommendation.external_record_id == "app-1"
    # OFAC is the single highest-severity signal on the platform — no
    # other event type scores higher, since sanctions screening has no
    # override path anywhere else either.
    assert evaluation.recommendation.risk_score >= 75


async def test_title_disclosure_override_always_flagged(db_session):
    payload = {"vehicle_id": "vehicle-1", "vin": "1HGCM82633A004352", "override_reason": "salesperson disclosed verbally, listing synced before paperwork caught up"}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "vhr.title_issue_disclosure_overridden", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "dms"
    assert evaluation.recommendation.action_type == "review_title_disclosure_override"
    assert evaluation.recommendation.external_record_id == "vehicle-1"


async def test_auction_overbid_override_always_flagged(db_session):
    payload = {"bid_id": "bid-1", "listing_id": "listing-1", "rooftop_id": "rooftop-1", "max_bid_usd": 45000.0, "override_reason": "presold to a waiting customer"}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "auction.overbid_book_override", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "dms"
    assert evaluation.recommendation.action_type == "review_auction_overbid"
    assert evaluation.recommendation.external_record_id == "bid-1"


async def test_card_geographic_anomaly_always_flagged(db_session):
    payload = {"tap_id": "tap-1", "card_id": "card-1", "hub_id": "hub-1", "reason": "implied travel speed 45,120 km/h between Miami Hub and LA Hub"}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "card.geographic_anomaly_detected", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "hub"
    assert evaluation.recommendation.action_type == "review_card_geographic_anomaly"
    assert evaluation.recommendation.external_record_id == "tap-1"
    assert evaluation.recommendation.risk_score >= 65


async def test_card_anomaly_live_review_uses_tap_history_tool_not_rbac(db_session, monkeypatch):
    """A hub-security event should get the card tap-history tool, not
    the RBAC tool set used for identity/access events — this pins that
    the tool set actually switches by domain rather than always
    defaulting to RBAC."""
    from unittest.mock import MagicMock

    from src.core.anthropic_client import AgentTurnResult, ToolCallResult
    from src.services import hub_tools

    monkeypatch.setattr(
        hub_tools, "build_card_tap_history_tool_dispatch",
        lambda tenant_id: {"get_card_tap_history": lambda inp: {"taps": []}},
    )

    fake_run_agent_turn = MagicMock(
        return_value=AgentTurnResult(
            final_text="First anomaly on this card — recommend verifying the member before reactivating.",
            tool_calls=[ToolCallResult(tool_name="get_card_tap_history", tool_input={"card_id": "card-1"}, tool_output={"taps": []})],
            stop_reason="end_turn",
        )
    )

    class _FakeClient:
        is_configured = True
        run_agent_turn = fake_run_agent_turn

    payload = {"tap_id": "tap-1", "card_id": "card-1", "hub_id": "hub-1", "reason": "implied travel speed 45,000 km/h"}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "card.geographic_anomaly_detected", payload, client=_FakeClient()
    )

    call_kwargs = fake_run_agent_turn.call_args.kwargs
    tool_names = {t["name"] for t in call_kwargs["tools"]}
    assert tool_names == {"get_card_tap_history"}
    assert evaluation.recommendation.reasoning == "First anomaly on this card — recommend verifying the member before reactivating."


async def test_identity_event_still_uses_rbac_tools_not_hub_tools(db_session, monkeypatch):
    """The domain switch must not have broken the original RBAC path —
    identity/access events still get RBAC tools, not hub tools."""
    from unittest.mock import MagicMock

    from src.core.anthropic_client import AgentTurnResult, ToolCallResult

    fake_run_agent_turn = MagicMock(
        return_value=AgentTurnResult(
            final_text="Elevated risk.",
            tool_calls=[ToolCallResult(tool_name="get_user_rbac_context", tool_input={"user_id": "user-1"}, tool_output={"active_roles": [], "active_overrides": []})],
            stop_reason="end_turn",
        )
    )

    class _FakeClient:
        is_configured = True
        run_agent_turn = fake_run_agent_turn

    payload = {"user_id": "user-1", "permission": "billing.refund", "granted_by": "agent-x"}
    await compliance_audit_agent.evaluate_event(db_session, TENANT, "permission.granted", payload, client=_FakeClient())

    call_kwargs = fake_run_agent_turn.call_args.kwargs
    tool_names = {t["name"] for t in call_kwargs["tools"]}
    assert "get_user_rbac_context" in tool_names
    assert "get_card_tap_history" not in tool_names


async def test_disparate_impact_large_spread_flagged_for_review(db_session, monkeypatch):
    from src.services import org_tools

    snapshot = {
        "groups": [
            {"origination_type": "subscription_enrollment", "approved": 80, "declined": 20, "total": 100, "decline_rate_pct": 20.0},
            {"origination_type": "rental_conversion", "approved": 40, "declined": 60, "total": 100, "decline_rate_pct": 60.0},
        ]
    }
    monkeypatch.setattr(org_tools, "get_disparate_impact_snapshot", lambda tenant_id: snapshot)

    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "org.decision_finalized", {"decision_id": "decision-1", "origination_type": "rental_conversion"}, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "org"
    assert evaluation.recommendation.action_type == "review_disparate_impact_pattern"
    assert evaluation.recommendation.external_record_id == "decision-1"
    assert evaluation.recommendation.risk_score >= 55


async def test_disparate_impact_small_spread_not_flagged(db_session, monkeypatch):
    from src.services import org_tools

    snapshot = {
        "groups": [
            {"origination_type": "subscription_enrollment", "approved": 90, "declined": 10, "total": 100, "decline_rate_pct": 10.0},
            {"origination_type": "rental_conversion", "approved": 87, "declined": 13, "total": 100, "decline_rate_pct": 13.0},
        ]
    }
    monkeypatch.setattr(org_tools, "get_disparate_impact_snapshot", lambda tenant_id: snapshot)

    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "org.decision_finalized", {"decision_id": "decision-2", "origination_type": "rental_conversion"}, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is None


async def test_disparate_impact_insufficient_sample_size_not_flagged(db_session, monkeypatch):
    from src.services import org_tools

    snapshot = {
        "groups": [
            {"origination_type": "subscription_enrollment", "approved": 1, "declined": 1, "total": 2, "decline_rate_pct": 50.0},
            {"origination_type": "rental_conversion", "approved": 0, "declined": 1, "total": 1, "decline_rate_pct": 100.0},
        ]
    }
    monkeypatch.setattr(org_tools, "get_disparate_impact_snapshot", lambda tenant_id: snapshot)

    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "org.decision_finalized", {"decision_id": "decision-3", "origination_type": "rental_conversion"}, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is None


async def test_disparate_impact_moderate_spread_scored_but_below_threshold(db_session, monkeypatch):
    from src.services import org_tools

    snapshot = {
        "groups": [
            {"origination_type": "subscription_enrollment", "approved": 85, "declined": 15, "total": 100, "decline_rate_pct": 15.0},
            {"origination_type": "rental_conversion", "approved": 74, "declined": 26, "total": 100, "decline_rate_pct": 26.0},
        ]
    }
    monkeypatch.setattr(org_tools, "get_disparate_impact_snapshot", lambda tenant_id: snapshot)

    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "org.decision_finalized", {"decision_id": "decision-4", "origination_type": "rental_conversion"}, client=_UnconfiguredClient()
    )
    # 11-point spread scores 25, below the default 50 threshold — logged but not escalated
    assert evaluation.recommendation is None
    assert evaluation.decision_log.reasoning  # still recorded a real assessment


async def test_disparate_impact_live_claude_uses_snapshot_tool_not_rbac(db_session, monkeypatch):
    from src.services import org_tools

    snapshot = {"groups": [{"origination_type": "rental_conversion", "approved": 40, "declined": 60, "total": 100, "decline_rate_pct": 60.0}]}
    monkeypatch.setattr(org_tools, "get_disparate_impact_snapshot", lambda tenant_id: snapshot)
    monkeypatch.setattr(org_tools, "build_disparate_impact_tool_dispatch", lambda tenant_id: {"get_disparate_impact_snapshot": lambda inp: snapshot})

    fake_run_agent_turn = MagicMock(
        return_value=AgentTurnResult(
            final_text="Only one group has adequate sample size — can't assess disparity yet.",
            tool_calls=[ToolCallResult(tool_name="get_disparate_impact_snapshot", tool_input={}, tool_output=snapshot)],
            stop_reason="end_turn",
        )
    )

    class _FakeClient:
        is_configured = True
        run_agent_turn = fake_run_agent_turn

    await compliance_audit_agent.evaluate_event(db_session, TENANT, "org.decision_finalized", {"decision_id": "decision-5"}, client=_FakeClient())

    call_kwargs = fake_run_agent_turn.call_args.kwargs
    tool_names = {t["name"] for t in call_kwargs["tools"]}
    assert "get_disparate_impact_snapshot" in tool_names
    assert "get_user_rbac_context" not in tool_names


async def test_deposit_capture_always_flagged(db_session):
    payload = {"hold_id": "hold-1", "amount_usd": 2000.0, "reason": "Windshield damage"}
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, TENANT, "deposit.capture_requested", payload, client=_UnconfiguredClient()
    )
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.domain == "subscription"
    assert evaluation.recommendation.action_type == "review_deposit_capture"
    assert evaluation.recommendation.external_record_id == "hold-1"


async def test_deposit_capture_near_total_forfeiture_scores_higher(db_session, monkeypatch):
    from src.services import subscription_tools

    monkeypatch.setattr(subscription_tools, "get_deposit_hold", lambda tenant_id, hold_id: {"id": hold_id, "amount_usd": 10000.0})

    small_payload = {"hold_id": "hold-1", "amount_usd": 500.0, "reason": "Minor cleaning fee"}
    small_eval = await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", small_payload, client=_UnconfiguredClient())

    large_payload = {"hold_id": "hold-2", "amount_usd": 9000.0, "reason": "Total loss deductible"}
    large_eval = await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", large_payload, client=_UnconfiguredClient())

    assert large_eval.recommendation.risk_score > small_eval.recommendation.risk_score


async def test_deposit_capture_fetches_real_hold_context(db_session, monkeypatch):
    from src.services import subscription_tools

    fetched = []
    monkeypatch.setattr(subscription_tools, "get_deposit_hold", lambda tenant_id, hold_id: fetched.append(hold_id) or {"id": hold_id, "amount_usd": 15000.0})

    payload = {"hold_id": "hold-3", "amount_usd": 1000.0, "reason": "Minor scratch"}
    await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", payload, client=_UnconfiguredClient())

    assert fetched == ["hold-3"]


async def test_deposit_capture_degrades_gracefully_when_subscription_svc_unreachable(db_session, monkeypatch):
    from src.services import subscription_tools

    def _raise(tenant_id, hold_id):
        raise ConnectionError("subscription-svc unreachable")

    monkeypatch.setattr(subscription_tools, "get_deposit_hold", _raise)

    payload = {"hold_id": "hold-4", "amount_usd": 3000.0, "reason": "Damage claim"}
    evaluation = await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", payload, client=_UnconfiguredClient())

    # Still flagged for review on the payload's own amount alone — an
    # unreachable dependency never silently drops the human review.
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.action_type == "review_deposit_capture"


async def test_deposit_capture_structural_gate_is_independent_of_ai_score(db_session, monkeypatch):
    """Even if the agent were paused or scored this at zero, the real
    enforcement is deposit_engine's state machine in subscription-svc
    (request_capture always lands in CAPTURE_PENDING_REVIEW) — this
    agent's recommendation is an additional signal, not the gate
    itself. Documented here so the two mechanisms don't get confused."""
    payload = {"hold_id": "hold-5", "amount_usd": 100.0, "reason": "Trivial fee"}
    evaluation = await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", payload, client=_UnconfiguredClient())
    assert evaluation.recommendation.risk_score == 55.0  # base score, always flagged regardless of amount — the hold is gated regardless either way


async def test_deposit_capture_always_creates_a_recommendation(db_session, monkeypatch):
    """Matches gu.fraud_suspected's own semantic: discretionary money
    taken from a member is always reviewed, not just large amounts —
    the base score alone crosses the default threshold."""
    from src.services import subscription_tools

    monkeypatch.setattr(subscription_tools, "get_deposit_hold", lambda tenant_id, hold_id: {"id": hold_id, "amount_usd": 10000.0})
    payload = {"hold_id": "hold-2", "amount_usd": 2000.0, "reason": "Minor cleaning fee"}
    evaluation = await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", payload, client=_UnconfiguredClient())

    assert evaluation.recommendation is not None
    assert evaluation.recommendation.risk_score == 55.0


async def test_deposit_capture_near_total_forfeiture_scores_higher_than_a_modest_one(db_session, monkeypatch):
    """Both cross the always-review threshold, but the near-total one
    scores higher — a real prioritization signal within the review
    queue, not just a binary flag/no-flag."""
    from src.services import subscription_tools

    monkeypatch.setattr(subscription_tools, "get_deposit_hold", lambda tenant_id, hold_id: {"id": hold_id, "amount_usd": 10000.0})

    modest = await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", {"hold_id": "hold-a", "amount_usd": 2000.0, "reason": "Minor"}, client=_UnconfiguredClient())
    near_total = await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", {"hold_id": "hold-b", "amount_usd": 8000.0, "reason": "Total loss"}, client=_UnconfiguredClient())

    assert near_total.recommendation.risk_score > modest.recommendation.risk_score
    assert near_total.recommendation.risk_score == 75.0


async def test_deposit_capture_recommendation_shape(db_session, monkeypatch):
    from src.services import subscription_tools

    monkeypatch.setattr(subscription_tools, "get_deposit_hold", lambda tenant_id, hold_id: {"id": hold_id, "amount_usd": 10000.0})
    payload = {"hold_id": "hold-1", "amount_usd": 8000.0, "reason": "Total loss during rental"}
    evaluation = await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", payload, client=_UnconfiguredClient())

    assert evaluation.recommendation.domain == "subscription"
    assert evaluation.recommendation.action_type == "review_deposit_capture"
    assert evaluation.recommendation.external_record_id == "hold-1"


async def test_deposit_capture_context_fetch_failure_degrades_gracefully(db_session, monkeypatch):
    """subscription-svc unreachable — the evaluation still runs on the
    payload's own amount_usd rather than blocking or erroring."""
    from src.services import subscription_tools

    def _raise(tenant_id, hold_id):
        raise ConnectionError("subscription-svc unreachable")

    monkeypatch.setattr(subscription_tools, "get_deposit_hold", _raise)
    payload = {"hold_id": "hold-3", "amount_usd": 500.0, "reason": "Disputed"}
    evaluation = await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", payload, client=_UnconfiguredClient())

    assert evaluation.decision_log.outcome != "error"


async def test_deposit_capture_without_hold_id_still_scores_on_payload_amount_alone(db_session):
    """No hold_id at all (e.g., a malformed event) — the near-total
    check simply can't fire since there's nothing to compare against,
    but the base "always reviewed" score still applies."""
    payload = {"amount_usd": 3000.0, "reason": "No hold_id provided"}
    evaluation = await compliance_audit_agent.evaluate_event(db_session, TENANT, "deposit.capture_requested", payload, client=_UnconfiguredClient())
    assert evaluation.recommendation is not None
    assert evaluation.recommendation.risk_score == 55.0


@pytest.mark.parametrize("event_type,payload,tier", [
    ("vehicle.tamper_detected", {"event_id": "t-1", "vehicle_id": "v-1", "detected_signal": "gps_jamming"}, 3),
    ("access.break_glass_activated", {"session_id": "s-1", "user_id": "u-1", "off_hours": True, "duration_minutes": 90}, 2),
])
async def test_review_cards_carry_the_tier_of_the_decision_they_ask_for(db_session, event_type, payload, tier):
    """A tamper event is tier 3 in the policy — its review card must
    demand a named accountable role; ordinary reviews are tier 2."""
    from src.services import compliance_audit_agent

    result = await compliance_audit_agent.evaluate_event(db_session, "tenant-x", event_type, payload)
    assert result.recommendation is not None
    assert result.recommendation.tier == tier
    assert result.recommendation.requires_accountable_role is (tier == 3)
