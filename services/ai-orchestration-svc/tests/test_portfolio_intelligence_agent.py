import pytest

from src.core.anthropic_client import AgentTurnResult, ToolCallResult
from src.services import portfolio_intelligence_agent as agent
from src.services import portfolio_tools
from src.services.supervisor import route_event
from tests.conftest import TENANT_HEADERS

TENANT = "voyqi-holdings"


class _Unconfigured:
    is_configured = False


def _alert(alert_id="a-1", metric_key="revenue_usd", observed=80.0, threshold=100.0, entity="voyqi-rental", key="subsidiary_key"):
    return {"id": alert_id, key: entity, "metric_key": metric_key, "observed_value": observed, "threshold_value": threshold, "status": "open"}


@pytest.mark.parametrize("observed,threshold,severity", [(80.0, 100.0, "high"), (70.0, 100.0, "critical"), (95.0, 100.0, "moderate")])
def test_severity_follows_deviation_from_threshold(observed, threshold, severity):
    assert agent.deterministic_assessment(_alert(observed=observed, threshold=threshold), [])[0] == severity


def test_covenant_headroom_is_always_critical():
    alert = _alert(metric_key="covenant_headroom", observed=9.5, threshold=10.0, key="funding_source_key", entity="revolver-1")
    severity, _cross, reasoning = agent.deterministic_assessment(alert, [])
    assert severity == "critical"
    assert "lender event" in reasoning


def test_same_metric_breaching_elsewhere_is_a_portfolio_pattern():
    alert = _alert(observed=96.0)
    peer = _alert(alert_id="a-2", observed=90.0, entity="voyqi-mobility")
    severity, cross, reasoning = agent.deterministic_assessment(alert, [alert, peer])
    assert cross is True
    assert severity == "high"  # small deviation, but not isolated
    assert "voyqi-mobility" in reasoning


async def test_kpi_alert_event_is_routed_and_logged(db_session, monkeypatch):
    monkeypatch.setattr(portfolio_tools, "get_open_alerts", lambda tenant_id, domain: [_alert(observed=60.0)])
    [result] = await route_event(db_session, TENANT, "executive.kpi_alert.triggered", {"alert_id": "a-1", "metric_key": "revenue_usd"})
    assert result.severity == "critical"
    log = result.decision_log
    assert log.agent_key == agent.AGENT_KEY
    assert log.action_taken == "kpi_alert_assessed_critical"
    assert (log.tier, log.approval_required) == (1, False)
    assert log.trigger_context["entity"] == "voyqi-rental"


async def test_alert_already_resolved_is_not_assessed(db_session, monkeypatch):
    monkeypatch.setattr(portfolio_tools, "get_open_alerts", lambda tenant_id, domain: [])
    result = await agent.evaluate_kpi_alert(db_session, TENANT, "capital_finance.kpi_alert.triggered", {"alert_id": "gone"}, client=_Unconfigured())
    assert result.severity is None
    assert result.decision_log.action_taken == "alert_not_open"


async def test_live_claude_can_escalate_but_never_lower_the_floor(db_session, monkeypatch):
    covenant = _alert(metric_key="covenant_headroom", observed=9.9, threshold=10.0, key="funding_source_key", entity="revolver-1")
    monkeypatch.setattr(portfolio_tools, "get_open_alerts", lambda tenant_id, domain: [covenant])

    class _Downplays:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="minor",
                tool_calls=[ToolCallResult("submit_alert_assessment", {}, {"severity": "moderate", "cross_subsidiary": False, "reasoning": "tiny miss"})],
                stop_reason="end_turn",
            )

    result = await agent.evaluate_kpi_alert(db_session, TENANT, "capital_finance.kpi_alert.triggered", {"alert_id": "a-1"}, client=_Downplays())
    assert result.severity == "critical"


def test_report_counts_alerts_and_flags_domains_without_data():
    snapshot = {
        "executive": {"summaries": {"revenue": {"totals": {"revenue_usd": 7_310_000}, "subsidiary_count": 3}}, "open_alerts": [_alert(observed=60.0)]},
        "management": {"summaries": {}, "open_alerts": []},
        "real_estate": {"summaries": {}, "open_alerts": []},
        "capital_finance": {"summaries": {}, "open_alerts": [_alert(metric_key="covenant_headroom", key="funding_source_key")]},
    }
    report = agent.build_report(snapshot)
    assert report["open_alerts"] == 2
    assert report["critical_alerts"] == 2
    assert report["domains"]["executive"]["totals"] == {"revenue": {"revenue_usd": 7_310_000}}
    assert report["domains_without_data"] == ["management", "real_estate"]


async def test_report_api_generates_and_returns_latest(client, monkeypatch):
    monkeypatch.setattr(portfolio_tools, "get_portfolio_snapshot", lambda tenant_id: {
        d: {"summaries": {}, "open_alerts": []} for d in portfolio_tools.DOMAINS
    })
    assert (await client.get("/api/v1/ai/portfolio/reports/latest", headers=TENANT_HEADERS)).status_code == 404
    created = (await client.post("/api/v1/ai/portfolio/reports", headers=TENANT_HEADERS)).json()
    latest = (await client.get("/api/v1/ai/portfolio/reports/latest", headers=TENANT_HEADERS)).json()
    assert latest["decision_log_id"] == created["decision_log_id"]
    assert latest["report"]["open_alerts"] == 0
