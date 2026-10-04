from unittest.mock import MagicMock

import pytest

from src.core.anthropic_client import AgentTurnResult, ToolCallResult
from src.models import Agent, AgentStatus
from src.services import fleet_intelligence_agent, fleet_tools

TENANT = "voyqi-holdings"


class _UnconfiguredClient:
    is_configured = False


class _FakeConfiguredClient:
    is_configured = True

    def __init__(self, tool_output):
        self.run_agent_turn = MagicMock(
            return_value=AgentTurnResult(
                final_text="Hub is short on executive-class vehicles; proposing a transfer.",
                tool_calls=[
                    ToolCallResult(tool_name="get_hub_supply", tool_input={"hub_id": "hub-1"}, tool_output={"executive": 0}),
                    ToolCallResult(tool_name="propose_transfer", tool_input={"vehicle_id": "veh-1", "to_hub_id": "hub-1"}, tool_output=tool_output),
                ],
                stop_reason="end_turn",
            )
        )


async def test_deterministic_fallback_proposes_transfer_when_below_threshold(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "get_hub_supply", lambda tenant_id, hub_id: {"executive": 0})
    calls = []
    monkeypatch.setattr(
        fleet_tools, "propose_transfer",
        lambda tenant_id, vehicle_id, to_hub_id, reason, agent_key: calls.append(
            (tenant_id, vehicle_id, to_hub_id, reason, agent_key)
        ) or {"id": "transfer-1", "status": "proposed"},
    )
    result = await fleet_intelligence_agent.evaluate_staging_need(
        db_session, TENANT, event_type="subscription.created", vehicle_id="veh-1",
        destination_hub_id="hub-1", context={"vehicle_class": "executive", "supply_threshold": 1},
        client=_UnconfiguredClient(),
    )
    assert result.proposed_transfer == {"id": "transfer-1", "status": "proposed"}
    assert "deterministic fallback" in result.decision_log.reasoning
    assert result.decision_log.action_taken == "transfer_proposed"
    assert len(calls) == 1
    assert calls[0][4] == fleet_intelligence_agent.AGENT_KEY


async def test_deterministic_fallback_no_transfer_when_supply_sufficient(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "get_hub_supply", lambda tenant_id, hub_id: {"executive": 5})
    calls = []
    monkeypatch.setattr(fleet_tools, "propose_transfer", lambda *a, **k: calls.append(1) or {})
    result = await fleet_intelligence_agent.evaluate_staging_need(
        db_session, TENANT, event_type="subscription.created", vehicle_id="veh-2",
        destination_hub_id="hub-2", context={"vehicle_class": "executive", "supply_threshold": 1},
        client=_UnconfiguredClient(),
    )
    assert result.proposed_transfer is None
    assert result.decision_log.action_taken == "no_transfer_needed"
    assert len(calls) == 0  # tool never called — no transfer proposed


async def test_paused_agent_evaluates_nothing(db_session, monkeypatch):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    calls = []
    monkeypatch.setattr(fleet_tools, "get_hub_supply", lambda *a, **k: calls.append(1) or {})
    result = await fleet_intelligence_agent.evaluate_staging_need(
        db_session, TENANT, event_type="subscription.created", vehicle_id="veh-3",
        destination_hub_id="hub-3", context={"vehicle_class": "executive"}, client=_UnconfiguredClient(),
    )
    assert result.proposed_transfer is None
    assert result.skipped_reason == "paused"
    assert len(calls) == 0


async def test_live_claude_path_uses_tool_calls(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "build_tool_dispatch", lambda tenant_id, agent_key: {"get_hub_supply": lambda inp: {}, "propose_transfer": lambda inp: {}})
    fake_client = _FakeConfiguredClient(tool_output={"id": "transfer-2", "status": "proposed"})
    result = await fleet_intelligence_agent.evaluate_staging_need(
        db_session, TENANT, event_type="travel.activated", vehicle_id="veh-4",
        destination_hub_id="hub-4", context={"member": "Priya S."}, client=fake_client,
    )
    fake_client.run_agent_turn.assert_called_once()
    assert result.proposed_transfer == {"id": "transfer-2", "status": "proposed"}
    assert any(tc["tool"] == "get_hub_supply" for tc in result.decision_log.tools_called)


async def test_agent_decision_count_increments(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "get_hub_supply", lambda *a, **k: {"executive": 5})
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent")
    db_session.add(agent_row)
    await db_session.commit()

    await fleet_intelligence_agent.evaluate_staging_need(
        db_session, TENANT, event_type="subscription.created", vehicle_id="veh-5",
        destination_hub_id="hub-5", context={"vehicle_class": "executive", "supply_threshold": 1},
        client=_UnconfiguredClient(),
    )
    await db_session.refresh(agent_row)
    assert agent_row.decisions_made == 1


async def test_handle_event_adapter_extracts_payload_fields(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "get_hub_supply", lambda *a, **k: {"urban": 5})
    payload = {"vehicle_id": "veh-6", "destination_hub_id": "hub-6", "vehicle_class": "urban", "supply_threshold": 1}
    result = await fleet_intelligence_agent.handle_event(db_session, TENANT, "vehicle.idle.48h", payload)
    assert result.decision_log.trigger_context["vehicle_id"] == "veh-6"
    assert result.decision_log.trigger_context["vehicle_class"] == "urban"


async def test_hub_rebalancing_deterministic_fallback_picks_nearest_with_headroom(db_session, monkeypatch):
    from src.services import hub_tools

    snapshot = {
        "hubs": [
            {"hub_id": "hub-1", "name": "Downtown Miami", "utilization_pct": 95.0, "latitude": 25.7743, "longitude": -80.1937},
            {"hub_id": "hub-2", "name": "Coral Gables", "utilization_pct": 40.0, "latitude": 25.7215, "longitude": -80.2684},
            {"hub_id": "hub-3", "name": "Austin Campus", "utilization_pct": 30.0, "latitude": 30.2849, "longitude": -97.7341},
        ]
    }
    monkeypatch.setattr(hub_tools, "get_network_snapshot", lambda tenant_id: snapshot)
    result = await fleet_intelligence_agent.evaluate_hub_rebalancing(
        db_session, TENANT, "hub-1", "vehicle not staged", client=_UnconfiguredClient()
    )
    assert result.candidate_hub_id == "hub-2"  # closer than Austin, both have headroom
    assert "Coral Gables" in result.reasoning


async def test_hub_rebalancing_no_candidate_when_all_at_capacity(db_session, monkeypatch):
    from src.services import hub_tools

    snapshot = {
        "hubs": [
            {"hub_id": "hub-1", "name": "Downtown Miami", "utilization_pct": 95.0, "latitude": 25.7743, "longitude": -80.1937},
            {"hub_id": "hub-2", "name": "Coral Gables", "utilization_pct": 92.0, "latitude": 25.7215, "longitude": -80.2684},
        ]
    }
    monkeypatch.setattr(hub_tools, "get_network_snapshot", lambda tenant_id: snapshot)
    result = await fleet_intelligence_agent.evaluate_hub_rebalancing(
        db_session, TENANT, "hub-1", "network check", client=_UnconfiguredClient()
    )
    assert result.candidate_hub_id is None
    assert "No other active hub" in result.reasoning


async def test_hub_rebalancing_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_hub_rebalancing(
        db_session, TENANT, "hub-1", "test", client=_UnconfiguredClient()
    )
    assert result.candidate_hub_id is None
    assert result.skipped_reason == "paused"


async def test_hub_rebalancing_live_claude_path_uses_submit_tool(db_session, monkeypatch):
    from src.services import hub_tools

    monkeypatch.setattr(hub_tools, "build_rebalancing_tool_dispatch", lambda tenant_id: {"get_network_snapshot": lambda inp: {}, "submit_rebalancing_recommendation": lambda inp: {}})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Coral Gables has real headroom and is close.",
                tool_calls=[
                    ToolCallResult(tool_name="get_network_snapshot", tool_input={}, tool_output={"hubs": []}),
                    ToolCallResult(
                        tool_name="submit_rebalancing_recommendation", tool_input={"candidate_hub_id": "hub-2", "reasoning": "closest hub with headroom"},
                        tool_output={"candidate_hub_id": "hub-2", "reasoning": "closest hub with headroom"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_hub_rebalancing(db_session, TENANT, "hub-1", "test", client=_FakeClient())
    assert result.candidate_hub_id == "hub-2"


async def test_handle_touchpoint_exception_adapter(db_session, monkeypatch):
    from src.services import hub_tools

    monkeypatch.setattr(hub_tools, "get_network_snapshot", lambda tenant_id: {"hubs": []})
    result = await fleet_intelligence_agent.handle_touchpoint_exception(
        db_session, TENANT, "hub.touchpoint_exception_flagged", {"hub_id": "hub-1", "reason": "vehicle not staged"}
    )
    assert result.candidate_hub_id is None  # no hubs in snapshot, honest no-candidate result
    assert result.decision_log.trigger_context["source_hub_id"] == "hub-1"


async def test_safety_score_no_events_stays_at_base_100(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "record_safety_score", lambda *a, **k: recorded.append(a) or {})

    result = await fleet_intelligence_agent.evaluate_safety_score(db_session, TENANT, "veh-1", {}, client=_UnconfiguredClient())
    assert result.score == 100.0
    assert len(recorded) == 1


async def test_safety_score_weights_match_exact_established_values(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_safety_score", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_safety_score(
        db_session, TENANT, "veh-2", {"harsh_braking": 2, "phone_usage_detected": 1, "forward_collision_warning": 1}, client=_UnconfiguredClient()
    )
    # 100 + (2 * -5) + (1 * -15) + (1 * -20) = 55
    assert result.score == 55.0


async def test_safety_score_floors_at_zero_never_negative(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_safety_score", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_safety_score(db_session, TENANT, "veh-3", {"forward_collision_warning": 10}, client=_UnconfiguredClient())
    assert result.score == 0.0


async def test_safety_score_unknown_event_types_contribute_nothing(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_safety_score", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_safety_score(db_session, TENANT, "veh-4", {"unknown_event": 5}, client=_UnconfiguredClient())
    assert result.score == 100.0


async def test_safety_score_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_safety_score(db_session, TENANT, "veh-5", {"speeding": 3}, client=_UnconfiguredClient())
    assert result.score is None
    assert result.skipped_reason == "paused"


async def test_safety_score_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_safety_score_tool_dispatch",
        lambda tenant_id: {"submit_safety_score": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "record_safety_score", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Elevated risk from repeated phone usage.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_safety_score", tool_input={"score": 62.0, "reasoning": "repeated phone usage detected, high real-world risk"},
                        tool_output={"score": 62.0, "reasoning": "repeated phone usage detected, high real-world risk"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_safety_score(db_session, TENANT, "veh-6", {"phone_usage_detected": 3}, client=_FakeClient())
    assert result.score == 62.0


async def test_handle_safety_score_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_safety_score", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_safety_score_requested(
        db_session, TENANT, "flt.safety_score_requested", {"vehicle_id": "veh-7", "event_counts": {"speeding": 1}, "window_days": 30}
    )
    assert result.score == 90.0


async def test_intake_readiness_failed_inspection_always_holds(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "record_intake_readiness", lambda *a, **k: recorded.append(a) or {})

    result = await fleet_intelligence_agent.evaluate_intake_readiness(db_session, TENANT, "insp-1", False, "everything looks fine actually", client=_UnconfiguredClient())
    assert result.recommendation == "hold_for_review"
    assert len(recorded) == 1


async def test_intake_readiness_passed_with_concern_keyword_flags(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_intake_readiness", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_intake_readiness(db_session, TENANT, "insp-2", True, "minor scuff on rear bumper", client=_UnconfiguredClient())
    assert result.recommendation == "provision_with_flag"
    assert "scuff" in result.reasoning


async def test_intake_readiness_passed_clean_notes_is_ready(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_intake_readiness", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_intake_readiness(db_session, TENANT, "insp-3", True, "all systems normal", client=_UnconfiguredClient())
    assert result.recommendation == "provision_ready"


async def test_intake_readiness_never_overrides_failed_inspection_despite_clean_notes(db_session, monkeypatch):
    """The core safety guarantee: even if notes read clean, a failed
    inspection can never be recommended as ready."""
    monkeypatch.setattr(fleet_tools, "record_intake_readiness", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_intake_readiness(db_session, TENANT, "insp-4", False, "no issues found, looks great", client=_UnconfiguredClient())
    assert result.recommendation == "hold_for_review"


async def test_intake_readiness_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_intake_readiness(db_session, TENANT, "insp-5", True, "notes", client=_UnconfiguredClient())
    assert result.recommendation is None
    assert result.skipped_reason == "paused"


async def test_intake_readiness_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_intake_readiness_tool_dispatch",
        lambda tenant_id: {"submit_readiness_recommendation": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "record_intake_readiness", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Flagging for tracking.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_readiness_recommendation", tool_input={"recommendation": "provision_with_flag", "reasoning": "cosmetic note worth tracking"},
                        tool_output={"recommendation": "provision_with_flag", "reasoning": "cosmetic note worth tracking"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_intake_readiness(db_session, TENANT, "insp-6", True, "small paint chip", client=_FakeClient())
    assert result.recommendation == "provision_with_flag"


async def test_handle_intake_readiness_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_intake_readiness", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_intake_readiness_requested(
        db_session, TENANT, "flt.intake_readiness_requested", {"intake_inspection_id": "insp-7", "passed": True, "notes": "clean"}
    )
    assert result.recommendation == "provision_ready"


async def test_telemetry_anomaly_short_distance_is_benign(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "record_telemetry_anomaly", lambda *a, **k: recorded.append(a) or {})

    result = await fleet_intelligence_agent.evaluate_telemetry_anomaly(db_session, TENANT, "alert-1", 80.0, client=_UnconfiguredClient())
    assert result.assessment == "benign"
    assert len(recorded) == 1


async def test_telemetry_anomaly_long_distance_needs_investigation(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_telemetry_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_telemetry_anomaly(db_session, TENANT, "alert-2", 300.0, client=_UnconfiguredClient())
    assert result.assessment == "needs_investigation"


async def test_telemetry_anomaly_at_exact_threshold_needs_investigation(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_telemetry_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_telemetry_anomaly(db_session, TENANT, "alert-3", 150.0, client=_UnconfiguredClient())
    assert result.assessment == "needs_investigation"


async def test_telemetry_anomaly_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_telemetry_anomaly(db_session, TENANT, "alert-4", 200.0, client=_UnconfiguredClient())
    assert result.assessment is None
    assert result.skipped_reason == "paused"


async def test_telemetry_anomaly_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_telemetry_anomaly_tool_dispatch",
        lambda tenant_id: {"submit_anomaly_assessment": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "record_telemetry_anomaly", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Consistent with a known supported route.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_anomaly_assessment", tool_input={"assessment": "benign", "reasoning": "matches a known member road trip pattern"},
                        tool_output={"assessment": "benign", "reasoning": "matches a known member road trip pattern"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_telemetry_anomaly(db_session, TENANT, "alert-5", 90.0, client=_FakeClient())
    assert result.assessment == "benign"


async def test_handle_telemetry_anomaly_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_telemetry_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_telemetry_anomaly_requested(db_session, TENANT, "flt.telemetry_anomaly_requested", {"geofence_alert_id": "alert-6", "distance_miles": 60.0})
    assert result.assessment == "benign"


async def test_maintenance_triage_urgent_always_ranks_before_predictive_and_routine(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "record_maintenance_triage", lambda *a, **k: recorded.append(a) or {})

    alerts = [
        {"alert_id": "a1", "vehicle_id": "veh-routine", "severity": "routine", "dtc_codes": []},
        {"alert_id": "a2", "vehicle_id": "veh-urgent", "severity": "urgent", "dtc_codes": ["P0300"]},
        {"alert_id": "a3", "vehicle_id": "veh-predictive", "severity": "predictive", "dtc_codes": []},
    ]
    result = await fleet_intelligence_agent.evaluate_maintenance_triage(db_session, TENANT, alerts, client=_UnconfiguredClient())
    assert result.priority_order[0] == "veh-urgent"
    assert result.priority_order[-1] == "veh-routine"
    assert len(recorded) == 1


async def test_maintenance_triage_tiebreaks_same_severity_by_dtc_count(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_maintenance_triage", lambda *a, **k: {})

    alerts = [
        {"alert_id": "a1", "vehicle_id": "veh-one-code", "severity": "urgent", "dtc_codes": ["P0300"]},
        {"alert_id": "a2", "vehicle_id": "veh-two-codes", "severity": "urgent", "dtc_codes": ["P0300", "P0301"]},
    ]
    result = await fleet_intelligence_agent.evaluate_maintenance_triage(db_session, TENANT, alerts, client=_UnconfiguredClient())
    assert result.priority_order[0] == "veh-two-codes"


async def test_maintenance_triage_empty_alert_list(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_maintenance_triage", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_maintenance_triage(db_session, TENANT, [], client=_UnconfiguredClient())
    assert result.priority_order == []


async def test_maintenance_triage_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_maintenance_triage(db_session, TENANT, [{"alert_id": "a1", "vehicle_id": "veh-1", "severity": "urgent", "dtc_codes": []}], client=_UnconfiguredClient())
    assert result.priority_order == []
    assert result.skipped_reason == "paused"


async def test_maintenance_triage_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_maintenance_triage_tool_dispatch",
        lambda tenant_id: {"submit_triage_priority": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "record_maintenance_triage", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Urgent DTC vehicle takes priority.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_triage_priority", tool_input={"priority_order": ["veh-urgent", "veh-routine"], "reasoning": "urgent DTC codes indicate real fault"},
                        tool_output={"priority_order": ["veh-urgent", "veh-routine"], "reasoning": "urgent DTC codes indicate real fault"},
                    ),
                ],
                stop_reason="end_turn",
            )

    alerts = [{"alert_id": "a1", "vehicle_id": "veh-urgent", "severity": "urgent", "dtc_codes": ["P0300"]}, {"alert_id": "a2", "vehicle_id": "veh-routine", "severity": "routine", "dtc_codes": []}]
    result = await fleet_intelligence_agent.evaluate_maintenance_triage(db_session, TENANT, alerts, client=_FakeClient())
    assert result.priority_order == ["veh-urgent", "veh-routine"]


async def test_handle_maintenance_triage_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_maintenance_triage", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_maintenance_triage_requested(
        db_session, TENANT, "flt.maintenance_triage_requested", {"alerts": [{"alert_id": "a1", "vehicle_id": "veh-1", "severity": "urgent", "dtc_codes": []}]}
    )
    assert result.priority_order == ["veh-1"]


async def test_incident_severity_severe_keywords_win(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "record_incident_severity", lambda *a, **k: recorded.append(a) or {})

    result = await fleet_intelligence_agent.evaluate_incident_severity(db_session, TENANT, "inc-1", "accident", "minor fender bender but airbag deployed", client=_UnconfiguredClient())
    assert result.severity == "severe"
    assert len(recorded) == 1


async def test_incident_severity_minor_keywords_when_no_severe_present(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_incident_severity", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_incident_severity(db_session, TENANT, "inc-2", "damage", "small scratch on rear bumper", client=_UnconfiguredClient())
    assert result.severity == "minor"


async def test_incident_severity_defaults_to_moderate_with_no_keyword_match(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_incident_severity", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_incident_severity(db_session, TENANT, "inc-3", "accident", "vehicles collided at intersection", client=_UnconfiguredClient())
    assert result.severity == "moderate"


async def test_incident_severity_never_calls_any_liability_related_function(db_session, monkeypatch):
    """The core safety guarantee for this module: no matter how the
    fleet_tools module is inspected, this capability never has access
    to anything resembling a liability-setting function."""
    monkeypatch.setattr(fleet_tools, "record_incident_severity", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_incident_severity(db_session, TENANT, "inc-4", "accident", "clearly the other driver ran a red light and hit our vehicle", client=_UnconfiguredClient())
    # even with description text that reads like an obvious liability call, only severity comes back
    assert result.severity in ("minor", "moderate", "severe")
    assert not hasattr(fleet_tools, "record_liability_assessment")
    assert not hasattr(fleet_tools, "assess_liability")


async def test_incident_severity_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_incident_severity(db_session, TENANT, "inc-5", "accident", "collision", client=_UnconfiguredClient())
    assert result.severity is None
    assert result.skipped_reason == "paused"


async def test_incident_severity_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_incident_severity_tool_dispatch",
        lambda tenant_id: {"submit_incident_severity": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "record_incident_severity", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Severe due to reported injury.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_incident_severity", tool_input={"severity": "severe", "summary": "collision with reported injury", "reasoning": "injury mentioned in description"},
                        tool_output={"severity": "severe", "summary": "collision with reported injury", "reasoning": "injury mentioned in description"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_incident_severity(db_session, TENANT, "inc-6", "accident", "collision, driver reported minor injury", client=_FakeClient())
    assert result.severity == "severe"


async def test_handle_incident_severity_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_incident_severity", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_incident_severity_requested(
        db_session, TENANT, "flt.incident_severity_requested", {"incident_id": "inc-7", "incident_type": "damage", "description": "small dent"}
    )
    assert result.severity == "minor"


async def test_valuation_anomaly_flags_when_market_well_below_book(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "record_valuation_anomaly", lambda *a, **k: recorded.append(a) or {})

    result = await fleet_intelligence_agent.evaluate_valuation_anomaly(db_session, TENANT, "veh-1", "val-1", 50000.0, 30000.0, client=_UnconfiguredClient())
    assert result.assessment == "flagged"
    assert result.deviation_pct == pytest.approx(-0.4)
    assert len(recorded) == 1


async def test_valuation_anomaly_flags_when_market_well_above_book(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_valuation_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_valuation_anomaly(db_session, TENANT, "veh-2", "val-2", 30000.0, 40000.0, client=_UnconfiguredClient())
    assert result.assessment == "flagged"
    assert result.deviation_pct == pytest.approx(0.3333, abs=0.001)


async def test_valuation_anomaly_normal_within_threshold(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_valuation_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_valuation_anomaly(db_session, TENANT, "veh-3", "val-3", 50000.0, 45000.0, client=_UnconfiguredClient())
    assert result.assessment == "normal"


async def test_valuation_anomaly_at_exact_threshold_flags(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_valuation_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_valuation_anomaly(db_session, TENANT, "veh-4", "val-4", 50000.0, 40000.0, client=_UnconfiguredClient())  # exactly -20%
    assert result.assessment == "flagged"


async def test_valuation_anomaly_zero_book_value_handled_safely(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_valuation_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_valuation_anomaly(db_session, TENANT, "veh-5", "val-5", 0.0, 10000.0, client=_UnconfiguredClient())
    assert result.assessment == "normal"
    assert result.deviation_pct == 0.0


async def test_valuation_anomaly_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_valuation_anomaly(db_session, TENANT, "veh-6", "val-6", 50000.0, 30000.0, client=_UnconfiguredClient())
    assert result.assessment is None
    assert result.skipped_reason == "paused"


async def test_valuation_anomaly_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_valuation_anomaly_tool_dispatch",
        lambda tenant_id: {"submit_valuation_anomaly": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "record_valuation_anomaly", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Significant drop, worth a look.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_valuation_anomaly", tool_input={"assessment": "flagged", "deviation_pct": -0.4, "reasoning": "market softened significantly for this class"},
                        tool_output={"assessment": "flagged", "deviation_pct": -0.4, "reasoning": "market softened significantly for this class"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_valuation_anomaly(db_session, TENANT, "veh-7", "val-7", 50000.0, 30000.0, client=_FakeClient())
    assert result.assessment == "flagged"


async def test_handle_valuation_anomaly_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_valuation_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_valuation_anomaly_requested(
        db_session, TENANT, "flt.valuation_anomaly_requested", {"vehicle_id": "veh-8", "asset_valuation_id": "val-8", "book_value_usd": 50000.0, "market_value_usd": 45000.0}
    )
    assert result.assessment == "normal"


async def test_disposition_channel_low_mileage_recommends_rideshare(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "create_disposition_channel_recommendation", lambda *a, **k: recorded.append(a) or {})

    result = await fleet_intelligence_agent.evaluate_disposition_channel(db_session, TENANT, "veh-1", 65000, "executive", client=_UnconfiguredClient())
    assert result.recommended_channel == "rideshare"
    assert len(recorded) == 1


async def test_disposition_channel_mid_mileage_recommends_local_rental(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "create_disposition_channel_recommendation", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_disposition_channel(db_session, TENANT, "veh-2", 80000, "urban", client=_UnconfiguredClient())
    assert result.recommended_channel == "local_rental"


async def test_disposition_channel_high_mileage_recommends_export(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "create_disposition_channel_recommendation", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_disposition_channel(db_session, TENANT, "veh-3", 100000, "urban", client=_UnconfiguredClient())
    assert result.recommended_channel == "export"


async def test_disposition_channel_very_high_mileage_recommends_parts(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "create_disposition_channel_recommendation", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_disposition_channel(db_session, TENANT, "veh-4", 150000, "urban", client=_UnconfiguredClient())
    assert result.recommended_channel == "parts"


async def test_disposition_channel_band_boundaries_are_exact(db_session, monkeypatch):
    """Verify each cascade band boundary lands on the correct side —
    the exact mileage that should tip from one stage to the next."""
    monkeypatch.setattr(fleet_tools, "create_disposition_channel_recommendation", lambda *a, **k: {})

    just_under_75k = await fleet_intelligence_agent.evaluate_disposition_channel(db_session, TENANT, "veh-5a", 74999, "urban", client=_UnconfiguredClient())
    at_75k = await fleet_intelligence_agent.evaluate_disposition_channel(db_session, TENANT, "veh-5b", 75000, "urban", client=_UnconfiguredClient())
    just_under_90k = await fleet_intelligence_agent.evaluate_disposition_channel(db_session, TENANT, "veh-5c", 89999, "urban", client=_UnconfiguredClient())
    at_120k = await fleet_intelligence_agent.evaluate_disposition_channel(db_session, TENANT, "veh-5d", 120000, "urban", client=_UnconfiguredClient())

    assert just_under_75k.recommended_channel == "rideshare"
    assert at_75k.recommended_channel == "local_rental"
    assert just_under_90k.recommended_channel == "local_rental"
    assert at_120k.recommended_channel == "parts"


async def test_disposition_channel_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_disposition_channel(db_session, TENANT, "veh-6", 65000, "urban", client=_UnconfiguredClient())
    assert result.recommended_channel is None
    assert result.skipped_reason == "paused"


async def test_disposition_channel_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_disposition_channel_tool_dispatch",
        lambda tenant_id: {"submit_disposition_channel": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "create_disposition_channel_recommendation", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Rideshare markets favor this class right now.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_disposition_channel", tool_input={"recommended_channel": "rideshare", "reasoning": "strong rideshare demand for executive class"},
                        tool_output={"recommended_channel": "rideshare", "reasoning": "strong rideshare demand for executive class"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_disposition_channel(db_session, TENANT, "veh-7", 65000, "executive", client=_FakeClient())
    assert result.recommended_channel == "rideshare"


async def test_handle_disposition_channel_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "create_disposition_channel_recommendation", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_disposition_channel_requested(
        db_session, TENANT, "flt.disposition_channel_requested", {"vehicle_id": "veh-8", "odometer_miles": 65000, "vehicle_class": "urban"}
    )
    assert result.recommended_channel == "rideshare"


async def test_covenant_risk_safe_well_below_limit(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "record_covenant_risk", lambda *a, **k: recorded.append(a) or {})

    result = await fleet_intelligence_agent.evaluate_covenant_risk(db_session, TENANT, "fac-1", 30.0, 90.0, client=_UnconfiguredClient())
    assert result.risk_level == "safe"
    assert len(recorded) == 1


async def test_covenant_risk_approaching_at_80_percent_of_limit(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_covenant_risk", lambda *a, **k: {})

    # 90% limit * 0.80 = 72% exactly triggers approaching
    result = await fleet_intelligence_agent.evaluate_covenant_risk(db_session, TENANT, "fac-2", 72.0, 90.0, client=_UnconfiguredClient())
    assert result.risk_level == "approaching"


async def test_covenant_risk_just_under_approaching_threshold_is_safe(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_covenant_risk", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_covenant_risk(db_session, TENANT, "fac-3", 71.9, 90.0, client=_UnconfiguredClient())
    assert result.risk_level == "safe"


async def test_covenant_risk_breached_over_limit(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_covenant_risk", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_covenant_risk(db_session, TENANT, "fac-4", 95.0, 90.0, client=_UnconfiguredClient())
    assert result.risk_level == "breached"


async def test_covenant_risk_exactly_at_limit_is_approaching_not_breached(db_session, monkeypatch):
    """The is_covenant_breached deterministic check in fleet-svc uses
    strictly-greater-than, so exactly at the limit is not a breach —
    this mirrors that exact boundary rather than introducing a
    different, conflicting one."""
    monkeypatch.setattr(fleet_tools, "record_covenant_risk", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_covenant_risk(db_session, TENANT, "fac-5", 90.0, 90.0, client=_UnconfiguredClient())
    assert result.risk_level == "approaching"


async def test_covenant_risk_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_covenant_risk(db_session, TENANT, "fac-6", 95.0, 90.0, client=_UnconfiguredClient())
    assert result.risk_level is None
    assert result.skipped_reason == "paused"


async def test_covenant_risk_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_covenant_risk_tool_dispatch",
        lambda tenant_id: {"submit_covenant_risk": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "record_covenant_risk", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Climbing quickly, worth a look.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_covenant_risk", tool_input={"risk_level": "approaching", "reasoning": "utilization has climbed sharply over recent pledges"},
                        tool_output={"risk_level": "approaching", "reasoning": "utilization has climbed sharply over recent pledges"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_covenant_risk(db_session, TENANT, "fac-7", 75.0, 90.0, client=_FakeClient())
    assert result.risk_level == "approaching"


async def test_handle_covenant_risk_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_covenant_risk", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_covenant_risk_requested(
        db_session, TENANT, "flt.covenant_risk_requested", {"facility_id": "fac-8", "utilization_pct": 30.0, "max_utilization_pct": 90.0}
    )
    assert result.risk_level == "safe"


async def test_fuel_anomaly_normal_gasoline_efficiency_and_cost(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "record_fuel_anomaly", lambda *a, **k: recorded.append(a) or {})

    result = await fleet_intelligence_agent.evaluate_fuel_anomaly(db_session, TENANT, "veh-1", "entry-1", "gasoline", 28.0, 3.50, client=_UnconfiguredClient())
    assert result.assessment == "normal"
    assert len(recorded) == 1


async def test_fuel_anomaly_implausibly_high_gasoline_efficiency_flags(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fuel_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fuel_anomaly(db_session, TENANT, "veh-2", "entry-2", "gasoline", 95.0, 3.50, client=_UnconfiguredClient())
    assert result.assessment == "flagged"
    assert "efficiency" in result.reasoning


async def test_fuel_anomaly_implausibly_low_gasoline_efficiency_flags(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fuel_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fuel_anomaly(db_session, TENANT, "veh-3", "entry-3", "gasoline", 3.0, 3.50, client=_UnconfiguredClient())
    assert result.assessment == "flagged"


async def test_fuel_anomaly_implausible_cost_per_gallon_flags(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fuel_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fuel_anomaly(db_session, TENANT, "veh-4", "entry-4", "gasoline", 28.0, 15.00, client=_UnconfiguredClient())
    assert result.assessment == "flagged"
    assert "cost" in result.reasoning


async def test_fuel_anomaly_normal_electric_efficiency_and_cost(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fuel_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fuel_anomaly(db_session, TENANT, "veh-5", "entry-5", "electric", 3.5, 0.15, client=_UnconfiguredClient())
    assert result.assessment == "normal"


async def test_fuel_anomaly_implausible_electric_efficiency_flags(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fuel_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fuel_anomaly(db_session, TENANT, "veh-6", "entry-6", "electric", 12.0, 0.15, client=_UnconfiguredClient())
    assert result.assessment == "flagged"


async def test_fuel_anomaly_none_values_produce_no_false_flags(db_session, monkeypatch):
    """Missing data (e.g. zero-quantity fill) should never register as
    an anomaly by default — absence of a signal is not itself a signal."""
    monkeypatch.setattr(fleet_tools, "record_fuel_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fuel_anomaly(db_session, TENANT, "veh-7", "entry-7", "gasoline", None, None, client=_UnconfiguredClient())
    assert result.assessment == "normal"


async def test_fuel_anomaly_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_fuel_anomaly(db_session, TENANT, "veh-8", "entry-8", "gasoline", 95.0, 3.50, client=_UnconfiguredClient())
    assert result.assessment is None
    assert result.skipped_reason == "paused"


async def test_fuel_anomaly_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_fuel_anomaly_tool_dispatch",
        lambda tenant_id: {"submit_fuel_anomaly": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "record_fuel_anomaly", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Implausible efficiency reading.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_fuel_anomaly", tool_input={"assessment": "flagged", "reasoning": "efficiency reading implausible for this vehicle class"},
                        tool_output={"assessment": "flagged", "reasoning": "efficiency reading implausible for this vehicle class"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_fuel_anomaly(db_session, TENANT, "veh-9", "entry-9", "gasoline", 95.0, 3.50, client=_FakeClient())
    assert result.assessment == "flagged"


async def test_handle_fuel_anomaly_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fuel_anomaly", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_fuel_anomaly_requested(
        db_session, TENANT, "flt.fuel_anomaly_requested", {"vehicle_id": "veh-10", "fuel_log_entry_id": "entry-10", "energy_type": "gasoline", "implied_efficiency": 28.0, "cost_per_unit_usd": 3.5}
    )
    assert result.assessment == "normal"


async def test_battery_degradation_trend_declining_projects_days(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "record_battery_degradation_forecast", lambda *a, **k: recorded.append(a) or {})

    # 95% -> 92% over 60 days = -0.05 pts/day; from 92% to 80% threshold is 12 points = 240 days
    result = await fleet_intelligence_agent.evaluate_battery_degradation_trend(db_session, TENANT, "veh-1", 95.0, 92.0, 60.0, 80.0, client=_UnconfiguredClient())
    assert result.estimated_days_to_threshold == 240
    assert len(recorded) == 1


async def test_battery_degradation_trend_flat_produces_no_forecast(db_session, monkeypatch):
    """A stable battery has no meaningful days-to-threshold — must be
    None, never a fabricated number."""
    monkeypatch.setattr(fleet_tools, "record_battery_degradation_forecast", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_battery_degradation_trend(db_session, TENANT, "veh-2", 92.0, 92.0, 60.0, 80.0, client=_UnconfiguredClient())
    assert result.estimated_days_to_threshold is None


async def test_battery_degradation_trend_improving_produces_no_forecast(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_battery_degradation_forecast", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_battery_degradation_trend(db_session, TENANT, "veh-3", 90.0, 92.0, 60.0, 80.0, client=_UnconfiguredClient())
    assert result.estimated_days_to_threshold is None


async def test_battery_degradation_trend_already_at_threshold_returns_zero_days(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_battery_degradation_forecast", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_battery_degradation_trend(db_session, TENANT, "veh-4", 85.0, 78.0, 60.0, 80.0, client=_UnconfiguredClient())
    assert result.estimated_days_to_threshold == 0


async def test_battery_degradation_trend_respects_custom_threshold(db_session, monkeypatch):
    """The threshold is passed in from fleet-svc's own config, not
    hardcoded here — verify a different threshold changes the
    projection correctly."""
    monkeypatch.setattr(fleet_tools, "record_battery_degradation_forecast", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_battery_degradation_trend(db_session, TENANT, "veh-5", 95.0, 92.0, 60.0, 70.0, client=_UnconfiguredClient())
    # from 92% to 70% threshold is 22 points at 0.05/day = 440 days
    assert result.estimated_days_to_threshold == 440


async def test_battery_degradation_trend_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_battery_degradation_trend(db_session, TENANT, "veh-6", 95.0, 92.0, 60.0, 80.0, client=_UnconfiguredClient())
    assert result.estimated_days_to_threshold is None
    assert result.skipped_reason == "paused"


async def test_battery_degradation_trend_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_battery_degradation_trend_tool_dispatch",
        lambda tenant_id: {"submit_battery_degradation_trend": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "record_battery_degradation_forecast", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Steady decline, budget for replacement in about 8 months.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_battery_degradation_trend", tool_input={"degradation_rate_pct_per_day": -0.05, "estimated_days_to_threshold": 240, "reasoning": "steady linear decline observed"},
                        tool_output={"degradation_rate_pct_per_day": -0.05, "estimated_days_to_threshold": 240, "reasoning": "steady linear decline observed"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_battery_degradation_trend(db_session, TENANT, "veh-7", 95.0, 92.0, 60.0, 80.0, client=_FakeClient())
    assert result.estimated_days_to_threshold == 240


async def test_handle_battery_degradation_trend_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_battery_degradation_forecast", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_battery_degradation_trend_requested(
        db_session, TENANT, "flt.battery_degradation_trend_requested", {"vehicle_id": "veh-8", "first_soh_pct": 95.0, "latest_soh_pct": 92.0, "days_elapsed": 60.0, "threshold_pct": 80.0}
    )
    assert result.estimated_days_to_threshold == 240


async def test_fleet_optimization_no_concerns_recommends_retain(db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(fleet_tools, "record_fleet_optimization", lambda *a, **k: recorded.append(a) or {})

    result = await fleet_intelligence_agent.evaluate_fleet_optimization(db_session, TENANT, "veh-1", 15000.0, 95.0, False, False, client=_UnconfiguredClient())
    assert result.recommendation == "retain"
    assert len(recorded) == 1


async def test_fleet_optimization_single_signal_recommends_review(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fleet_optimization", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fleet_optimization(db_session, TENANT, "veh-2", 15000.0, 95.0, True, False, client=_UnconfiguredClient())
    assert result.recommendation == "review_for_disposition"


async def test_fleet_optimization_two_signals_recommends_prioritize(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fleet_optimization", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fleet_optimization(db_session, TENANT, "veh-3", 25000.0, 70.0, True, False, client=_UnconfiguredClient())
    assert result.recommendation == "prioritize_disposition"


async def test_fleet_optimization_all_three_signals_recommends_prioritize(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fleet_optimization", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fleet_optimization(db_session, TENANT, "veh-4", 30000.0, 60.0, True, True, client=_UnconfiguredClient())
    assert result.recommendation == "prioritize_disposition"
    assert "eligibility" in result.reasoning
    assert "availability" in result.reasoning
    assert "valuation" in result.reasoning


async def test_fleet_optimization_low_availability_alone_recommends_review(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fleet_optimization", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fleet_optimization(db_session, TENANT, "veh-5", 15000.0, 70.0, False, False, client=_UnconfiguredClient())
    assert result.recommendation == "review_for_disposition"


async def test_fleet_optimization_missing_availability_data_does_not_falsely_score(db_session, monkeypatch):
    """Missing data (availability_pct=None, e.g. brand-new vehicle
    with no elapsed time) must never register as a concern signal by
    default."""
    monkeypatch.setattr(fleet_tools, "record_fleet_optimization", lambda *a, **k: {})

    result = await fleet_intelligence_agent.evaluate_fleet_optimization(db_session, TENANT, "veh-6", 15000.0, None, False, False, client=_UnconfiguredClient())
    assert result.recommendation == "retain"


async def test_fleet_optimization_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=fleet_intelligence_agent.AGENT_KEY, display_name="Fleet Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await fleet_intelligence_agent.evaluate_fleet_optimization(db_session, TENANT, "veh-7", 30000.0, 60.0, True, True, client=_UnconfiguredClient())
    assert result.recommendation is None
    assert result.skipped_reason == "paused"


async def test_fleet_optimization_live_claude_path(db_session, monkeypatch):
    monkeypatch.setattr(
        fleet_tools, "build_fleet_optimization_tool_dispatch",
        lambda tenant_id: {"submit_fleet_optimization": lambda inp: {}},
    )
    monkeypatch.setattr(fleet_tools, "record_fleet_optimization", lambda *a, **k: {})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Multiple converging signals favor disposition.",
                tool_calls=[
                    ToolCallResult(
                        tool_name="submit_fleet_optimization", tool_input={"recommendation": "prioritize_disposition", "reasoning": "eligible, poor availability, and a flagged valuation all point the same direction"},
                        tool_output={"recommendation": "prioritize_disposition", "reasoning": "eligible, poor availability, and a flagged valuation all point the same direction"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await fleet_intelligence_agent.evaluate_fleet_optimization(db_session, TENANT, "veh-8", 30000.0, 60.0, True, True, client=_FakeClient())
    assert result.recommendation == "prioritize_disposition"


async def test_handle_fleet_optimization_requested_adapter(db_session, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fleet_optimization", lambda *a, **k: {})

    result = await fleet_intelligence_agent.handle_fleet_optimization_requested(
        db_session, TENANT, "flt.fleet_optimization_requested", {"vehicle_id": "veh-9", "total_tco_usd": 15000.0, "availability_pct": 95.0, "is_disposition_eligible": False, "valuation_flagged": False}
    )
    assert result.recommendation == "retain"
