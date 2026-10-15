"""
Portfolio Intelligence Agent — batch tier, one shared agent across the
four lightly-loaded holding-company domains: executive-svc,
management-svc, real-estate-svc and capital-finance-svc
(docs/AI_ORCHESTRATION_SCALING.md, roster expansion).

Reporting and rollups only — tier 1 across the board, and there is no
tool in portfolio_tools that writes to any of the four services. Two
capabilities:

1. KPI alert assessment — reacts to <domain>.kpi_alert.triggered: how far
   past its threshold the metric is, whether the same metric is breaching
   at other subsidiaries/cost centers/properties/facilities (a portfolio
   pattern, not a local blip), and a severity. Covenant headroom is
   always critical: a financing covenant breach is a lender event.
2. Portfolio report — on portfolio.rollup.scheduled (or on demand): one
   cross-domain read of every dashboard summary and open alert.

Two-layer discipline: the deterministic fallback computes severity from
real deviation arithmetic; the live Claude path can read the other
domains before judging, but the severity floor for covenant headroom is
applied after either path — the model can raise it, never lower it.
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.anthropic_client import AnthropicAgentClient
from src.models import Agent, AgentStatus, DecisionLog
from src.services import portfolio_tools

AGENT_KEY = "portfolio_intelligence_agent"

ALERT_EVENT_DOMAINS = {
    "executive.kpi_alert.triggered": "executive",
    "management.kpi_alert.triggered": "management",
    "real_estate.kpi_alert.triggered": "real_estate",
    "capital_finance.kpi_alert.triggered": "capital_finance",
}
ENTITY_KEYS = ("subsidiary_key", "cost_center_key", "property_key", "funding_source_key")
CRITICAL_DEVIATION_PCT = 25.0
HIGH_DEVIATION_PCT = 10.0
ALWAYS_CRITICAL_METRICS = {"covenant_headroom", "covenant_headroom_pct"}
SEVERITY_ORDER = {"moderate": 0, "high": 1, "critical": 2}

ALERT_SYSTEM_PROMPT = """You are the Portfolio Intelligence Agent for VOYQI Holdings, \
assessing a KPI alert from one of the holding company's domains (executive P&L, \
shared-services operations, real estate, or capital & finance).

Use get_open_alerts across the other domains and get_domain_summary to judge whether \
this is an isolated breach or part of a portfolio-wide pattern, and how material it \
is relative to the totals. You only assess and report — nothing you do changes any \
record. Always finish by calling submit_alert_assessment."""


@dataclass
class AlertAssessmentResult:
    decision_log: DecisionLog
    severity: str | None
    cross_subsidiary: bool
    reasoning: str
    skipped_reason: str | None = None


@dataclass
class PortfolioReportResult:
    decision_log: DecisionLog
    report: dict | None
    skipped_reason: str | None = None


async def _agent_status(db: AsyncSession, tenant_id: str) -> AgentStatus:
    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    return agent_row.status if agent_row else AgentStatus.ACTIVE


async def _log(db: AsyncSession, tenant_id: str, trigger: str, context: dict, reasoning: str, tools_called: list, action: str, outcome: str) -> DecisionLog:
    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type=trigger, trigger_context=context,
        reasoning=reasoning, tools_called=tools_called, action_taken=action, outcome=outcome,
    )
    db.add(log)
    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row and outcome != "skipped":
        agent_row.decisions_made += 1
    await db.commit()
    await db.refresh(log)
    return log


def _entity(alert: dict) -> str | None:
    return next((alert[k] for k in ENTITY_KEYS if alert.get(k)), None)


def deviation_pct(observed: float, threshold: float) -> float:
    if threshold == 0:
        return 100.0 if observed != 0 else 0.0
    return abs(observed - threshold) / abs(threshold) * 100.0


def deterministic_assessment(alert: dict, peer_alerts: list[dict]) -> tuple[str, bool, str]:
    deviation = deviation_pct(alert["observed_value"], alert["threshold_value"])
    peers = sorted({_entity(p) for p in peer_alerts if p.get("metric_key") == alert["metric_key"] and p.get("id") != alert["id"]} - {None})
    cross = bool(peers)
    if alert["metric_key"] in ALWAYS_CRITICAL_METRICS or deviation >= CRITICAL_DEVIATION_PCT:
        severity = "critical"
    elif deviation >= HIGH_DEVIATION_PCT or cross:
        severity = "high"
    else:
        severity = "moderate"
    reasoning = (
        f"{alert['metric_key']} at {_entity(alert)} is {alert['observed_value']:,.2f} against a threshold of "
        f"{alert['threshold_value']:,.2f} ({deviation:.1f}% deviation)."
        + (f" The same metric is also breaching at {', '.join(peers)} — a portfolio pattern, not a local blip." if cross else "")
        + (" Covenant headroom breaches are always critical — a lender event." if alert["metric_key"] in ALWAYS_CRITICAL_METRICS else "")
    )
    return severity, cross, reasoning


async def evaluate_kpi_alert(
    db: AsyncSession, tenant_id: str, event_type: str, payload: dict, client: AnthropicAgentClient | None = None,
) -> AlertAssessmentResult:
    client = client or AnthropicAgentClient()
    domain = ALERT_EVENT_DOMAINS[event_type]
    context = {"domain": domain, "alert_id": payload.get("alert_id"), "metric_key": payload.get("metric_key")}

    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = await _log(db, tenant_id, event_type, context, f"Agent status is {status.value} — no action was taken.", [], "skipped_agent_not_active", "skipped")
        return AlertAssessmentResult(decision_log=log, severity=None, cross_subsidiary=False, reasoning=log.reasoning, skipped_reason=status.value)

    open_alerts = portfolio_tools.get_open_alerts(tenant_id, domain)
    tools_called: list[dict] = [{"tool": "get_open_alerts", "input": {"domain": domain}}]
    alert = next((a for a in open_alerts if a.get("id") == payload.get("alert_id")), None)
    if alert is None:
        reasoning = f"Alert {payload.get('alert_id')} is no longer open in {domain} (already resolved, or the service is unreachable) — nothing to assess."
        log = await _log(db, tenant_id, event_type, context, reasoning, tools_called, "alert_not_open", "deferred")
        return AlertAssessmentResult(decision_log=log, severity=None, cross_subsidiary=False, reasoning=reasoning)

    severity, cross, reasoning = deterministic_assessment(alert, open_alerts)
    if client.is_configured:
        turn = client.run_agent_turn(
            system_prompt=ALERT_SYSTEM_PROMPT,
            user_message=f"Domain: {domain}. Alert: {alert}. Deterministic pre-assessment: {severity} ({reasoning})",
            tools=portfolio_tools.ALERT_ASSESSMENT_TOOL_SCHEMAS,
            tool_dispatch=portfolio_tools.build_alert_assessment_tool_dispatch(tenant_id),
        )
        tools_called += [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        submitted = [tc.tool_output for tc in turn.tool_calls if tc.tool_name == "submit_alert_assessment"]
        if submitted:
            model_severity = submitted[-1].get("severity", severity)
            # The model may escalate, never de-escalate below the
            # deterministic floor (e.g. covenant headroom is always critical).
            if SEVERITY_ORDER.get(model_severity, -1) > SEVERITY_ORDER[severity]:
                severity = model_severity
            cross = cross or bool(submitted[-1].get("cross_subsidiary"))
            reasoning = submitted[-1].get("reasoning") or turn.final_text or reasoning
    else:
        reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {reasoning}"

    context.update({"entity": _entity(alert), "severity": severity, "cross_subsidiary": cross,
                    "observed_value": alert["observed_value"], "threshold_value": alert["threshold_value"]})
    log = await _log(db, tenant_id, event_type, context, reasoning, tools_called, f"kpi_alert_assessed_{severity}", "success")
    return AlertAssessmentResult(decision_log=log, severity=severity, cross_subsidiary=cross, reasoning=reasoning)


async def handle_kpi_alert(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> AlertAssessmentResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_kpi_alert(db, tenant_id, event_type, payload)


def build_report(snapshot: dict[str, Any]) -> dict:
    """Deterministic portfolio report from one cross-domain read."""
    domains = {}
    for domain, data in snapshot.items():
        alerts = data.get("open_alerts") or []
        assessed = [deterministic_assessment(a, alerts)[0] for a in alerts]
        domains[domain] = {
            "totals": {m: s.get("totals", {}) for m, s in data.get("summaries", {}).items() if s.get("totals")},
            "reporting_entities": max((s.get("subsidiary_count", 0) for s in data.get("summaries", {}).values()), default=0),
            "open_alerts": len(alerts),
            "critical_alerts": assessed.count("critical"),
        }
    return {
        "domains": domains,
        "open_alerts": sum(d["open_alerts"] for d in domains.values()),
        "critical_alerts": sum(d["critical_alerts"] for d in domains.values()),
        "domains_without_data": sorted(d for d, v in domains.items() if not v["totals"] and not v["open_alerts"]),
    }


async def generate_portfolio_report(db: AsyncSession, tenant_id: str, trigger: str = "portfolio.rollup.scheduled") -> PortfolioReportResult:
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = await _log(db, tenant_id, trigger, {}, f"Agent status is {status.value} — no action was taken.", [], "skipped_agent_not_active", "skipped")
        return PortfolioReportResult(decision_log=log, report=None, skipped_reason=status.value)
    report = build_report(portfolio_tools.get_portfolio_snapshot(tenant_id))
    reasoning = (
        f"Portfolio report: {report['open_alerts']} open KPI alert(s), {report['critical_alerts']} critical, "
        f"across {len(report['domains'])} domains."
        + (f" No data yet from: {', '.join(report['domains_without_data'])}." if report["domains_without_data"] else "")
    )
    log = await _log(
        db, tenant_id, trigger, {"report": report}, reasoning,
        [{"tool": "get_portfolio_snapshot", "input": {}}], "portfolio_report_generated", "success",
    )
    return PortfolioReportResult(decision_log=log, report=report)


async def handle_rollup_scheduled(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> PortfolioReportResult:
    return await generate_portfolio_report(db, tenant_id, event_type)
