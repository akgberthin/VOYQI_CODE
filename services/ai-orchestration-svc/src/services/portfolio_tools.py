"""Read-only tool adapters over the four Portfolio Intelligence services
(executive, management, real-estate, capital-finance). Portfolio
Intelligence is reporting/rollups only — there is deliberately no tool
here that writes to any of them."""
from typing import Any

import httpx

from src.core.config import get_settings

# domain -> (base-url setting, API path segment, metric domains it reports)
DOMAINS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "executive": ("executive_svc_base_url", "executive", ("revenue", "fleet", "workforce", "marketing")),
    "management": ("management_svc_base_url", "management", ("spend", "headcount", "utilization", "sla")),
    "real_estate": ("real_estate_svc_base_url", "real-estate", ("occupancy", "lease_cost", "maintenance", "utilization")),
    "capital_finance": ("capital_finance_svc_base_url", "capital-finance", ("balance", "utilization", "cost_of_capital", "covenant_headroom")),
}


def _headers(tenant_id: str) -> dict:
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": get_settings().ai_initiated_header_value}


def _get(domain: str, tenant_id: str, path: str, params: dict | None = None) -> Any:
    setting, segment, _metrics = DOMAINS[domain]
    base_url = getattr(get_settings(), setting)
    try:
        with httpx.Client(base_url=base_url, timeout=10.0) as client:
            resp = client.get(f"/api/v1/{segment}{path}", params=params, headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def get_open_alerts(tenant_id: str, domain: str) -> list[dict]:
    return _get(domain, tenant_id, "/alerts", {"status": "open"}) or []


def get_domain_summary(tenant_id: str, domain: str, metric_domain: str) -> dict:
    return _get(domain, tenant_id, "/dashboard/summary", {"metric_domain": metric_domain}) or {}


def get_portfolio_snapshot(tenant_id: str) -> dict:
    """Every domain's summaries and open alerts in one read — what the
    portfolio report is built from."""
    snapshot = {}
    for domain, (_setting, _segment, metric_domains) in DOMAINS.items():
        snapshot[domain] = {
            "summaries": {m: get_domain_summary(tenant_id, domain, m) for m in metric_domains},
            "open_alerts": get_open_alerts(tenant_id, domain),
        }
    return snapshot


def submit_alert_assessment(severity: str, cross_subsidiary: bool, reasoning: str) -> dict:
    return {"severity": severity, "cross_subsidiary": cross_subsidiary, "reasoning": reasoning}


ALERT_ASSESSMENT_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_open_alerts",
        "description": "Fetch every open KPI alert in one portfolio domain (executive, management, real_estate, capital_finance).",
        "input_schema": {"type": "object", "properties": {"domain": {"type": "string"}}, "required": ["domain"]},
    },
    {
        "name": "get_domain_summary",
        "description": "Fetch the cross-subsidiary totals for one metric domain within a portfolio domain.",
        "input_schema": {
            "type": "object",
            "properties": {"domain": {"type": "string"}, "metric_domain": {"type": "string"}},
            "required": ["domain", "metric_domain"],
        },
    },
    {
        "name": "submit_alert_assessment",
        "description": "Report the alert's severity (critical/high/moderate), whether the same metric is breaching across subsidiaries, and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": ["critical", "high", "moderate"]},
                "cross_subsidiary": {"type": "boolean"},
                "reasoning": {"type": "string"},
            },
            "required": ["severity", "cross_subsidiary", "reasoning"],
        },
    },
]


def build_alert_assessment_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_open_alerts": lambda inp: get_open_alerts(tenant_id, inp["domain"]),
        "get_domain_summary": lambda inp: get_domain_summary(tenant_id, inp["domain"], inp["metric_domain"]),
        "submit_alert_assessment": lambda inp: submit_alert_assessment(inp["severity"], inp["cross_subsidiary"], inp["reasoning"]),
    }
