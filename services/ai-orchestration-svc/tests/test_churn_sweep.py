import httpx

from src.batch import churn_sweep
from src.models import Agent, AgentStatus
from src.services import fleet_tools, member_experience_agent, subscription_tools

TENANT = "voyqi-holdings"


class _UnconfiguredClient:
    is_configured = False


def _member(member_id="mem-1", vehicle_id="veh-1"):
    return {"id": member_id, "full_name": "Test Member", "active_vehicle_id": vehicle_id}


async def test_context_includes_real_days_since_last_use_from_fleet_telemetry(monkeypatch):
    from datetime import datetime, timedelta, timezone

    recorded_at = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    monkeypatch.setattr(fleet_tools, "get_latest_telemetry", lambda tenant_id, vehicle_id: {"recorded_at": recorded_at})
    monkeypatch.setattr(subscription_tools, "get_pause_count", lambda tenant_id, member_id, window_days=365: 0)

    context = churn_sweep._build_churn_context(TENANT, _member())
    assert context["days_since_last_use"] in (44, 45)  # allow for the moment the test itself takes to run


async def test_context_omits_days_since_last_use_when_no_vehicle_assigned(monkeypatch):
    monkeypatch.setattr(subscription_tools, "get_pause_count", lambda tenant_id, member_id, window_days=365: 2)
    context = churn_sweep._build_churn_context(TENANT, _member(vehicle_id=None))
    assert "days_since_last_use" not in context
    assert context["pause_count_last_year"] == 2


async def test_context_omits_days_since_last_use_when_fleet_svc_unreachable(monkeypatch):
    def _raise(tenant_id, vehicle_id):
        raise httpx.HTTPError("fleet-svc unreachable")

    monkeypatch.setattr(fleet_tools, "get_latest_telemetry", _raise)
    monkeypatch.setattr(subscription_tools, "get_pause_count", lambda tenant_id, member_id, window_days=365: 0)

    context = churn_sweep._build_churn_context(TENANT, _member())
    assert "days_since_last_use" not in context


async def test_context_never_fabricates_a_zero_when_pause_data_unavailable(monkeypatch):
    """The one property this module cares most about: an unreachable
    subscription-svc must leave pause_count_last_year OUT of the
    context, never silently default it to 0, which would falsely
    assert 'definitely no pauses' instead of 'unknown.'"""
    def _raise(tenant_id, member_id, window_days=365):
        raise httpx.HTTPError("subscription-svc unreachable")

    monkeypatch.setattr(fleet_tools, "get_latest_telemetry", lambda tenant_id, vehicle_id: None)
    monkeypatch.setattr(subscription_tools, "get_pause_count", _raise)

    context = churn_sweep._build_churn_context(TENANT, _member())
    assert "pause_count_last_year" not in context


async def test_context_never_includes_support_complaints_key_at_all(monkeypatch):
    """No case-management system exists for subscription members yet
    — this key must never appear, fabricated or otherwise."""
    monkeypatch.setattr(fleet_tools, "get_latest_telemetry", lambda tenant_id, vehicle_id: None)
    monkeypatch.setattr(subscription_tools, "get_pause_count", lambda tenant_id, member_id, window_days=365: 1)

    context = churn_sweep._build_churn_context(TENANT, _member())
    assert "support_complaints_last_90d" not in context


async def test_run_churn_sweep_evaluates_every_member_with_context(db_session, monkeypatch):
    monkeypatch.setattr(subscription_tools, "list_members", lambda tenant_id, mobility_class=None: [_member("mem-1"), _member("mem-2")])
    monkeypatch.setattr(fleet_tools, "get_latest_telemetry", lambda tenant_id, vehicle_id: None)
    monkeypatch.setattr(subscription_tools, "get_pause_count", lambda tenant_id, member_id, window_days=365: 1)

    summary = await churn_sweep.run_churn_sweep(db_session, TENANT)
    assert summary["evaluated"] == 2
    assert summary["aborted"] is False


async def test_run_churn_sweep_counts_members_with_no_context_as_skipped_not_evaluated(db_session, monkeypatch):
    monkeypatch.setattr(subscription_tools, "list_members", lambda tenant_id, mobility_class=None: [_member("mem-1", vehicle_id=None)])

    def _raise(tenant_id, member_id, window_days=365):
        raise httpx.HTTPError("unreachable")

    monkeypatch.setattr(subscription_tools, "get_pause_count", _raise)

    summary = await churn_sweep.run_churn_sweep(db_session, TENANT)
    assert summary["evaluated"] == 0
    assert summary["skipped_no_context"] == 1


async def test_run_churn_sweep_counts_intervention_threshold_crossings(db_session, monkeypatch):
    monkeypatch.setattr(subscription_tools, "list_members", lambda tenant_id, mobility_class=None: [_member("mem-1")])
    monkeypatch.setattr(fleet_tools, "get_latest_telemetry", lambda tenant_id, vehicle_id: None)
    # 2 pauses alone (25 pts) won't cross the 50-point threshold on its own in the deterministic
    # scorer, so also force a real days_since_last_use signal high enough to cross it.
    from datetime import datetime, timedelta, timezone
    recorded_at = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
    monkeypatch.setattr(fleet_tools, "get_latest_telemetry", lambda tenant_id, vehicle_id: {"recorded_at": recorded_at})
    monkeypatch.setattr(subscription_tools, "get_pause_count", lambda tenant_id, member_id, window_days=365: 2)
    monkeypatch.setattr(subscription_tools, "award_retention_gu_bonus", lambda tenant_id, member_id, amount: {})

    summary = await churn_sweep.run_churn_sweep(db_session, TENANT)
    assert summary["evaluated"] == 1
    assert summary["crossed_intervention_threshold"] == 1


async def test_run_churn_sweep_aborts_cleanly_when_member_listing_fails(db_session, monkeypatch):
    def _raise(tenant_id, mobility_class=None):
        raise httpx.HTTPError("subscription-svc unreachable")

    monkeypatch.setattr(subscription_tools, "list_members", _raise)

    summary = await churn_sweep.run_churn_sweep(db_session, TENANT)
    assert summary["aborted"] is True
    assert summary["evaluated"] == 0


async def test_run_churn_sweep_respects_paused_agent(db_session, monkeypatch):
    agent_row = Agent(tenant_id=TENANT, key=member_experience_agent.AGENT_KEY, display_name="Member Experience Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    monkeypatch.setattr(subscription_tools, "list_members", lambda tenant_id, mobility_class=None: [_member("mem-1")])
    monkeypatch.setattr(fleet_tools, "get_latest_telemetry", lambda tenant_id, vehicle_id: None)
    monkeypatch.setattr(subscription_tools, "get_pause_count", lambda tenant_id, member_id, window_days=365: 1)

    summary = await churn_sweep.run_churn_sweep(db_session, TENANT)
    # evaluate_churn_risk still runs and logs a decision (skipped_agent_not_active),
    # so it counts as "evaluated" — the sweep ran, the agent just declined to score.
    assert summary["evaluated"] == 1
    assert summary["crossed_intervention_threshold"] == 0
