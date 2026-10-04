"""
Fleet tool adapters, mirroring rbac_tools.py / marketing_tools.py: real
HTTP calls into fleet-svc, exposed as Anthropic tool schemas for the
agent's own reasoning loop.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": settings.ai_initiated_header_value}


def get_latest_telemetry(tenant_id: str, vehicle_id: str) -> dict[str, Any] | None:
    """Returns None (not an empty dict) when no reading exists yet for
    this vehicle — callers must treat "never seen a telemetry reading"
    differently from "read something, but it's old," not conflate the
    two into a fabricated recency number."""
    settings = get_settings()
    with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/latest-telemetry", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def get_hub_supply(tenant_id: str, hub_id: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/fleet/hubs/{hub_id}/supply", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def get_vehicle(tenant_id: str, vehicle_id: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/fleet/vehicles/{vehicle_id}", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def propose_transfer(tenant_id: str, vehicle_id: str, to_hub_id: str, reason: str, agent_key: str) -> dict[str, Any]:
    """Proposes a transfer — this NEVER dispatches. fleet-svc's own
    workflow requires a human to dispatch (assign a driver/transport)
    before a vehicle actually moves; this tool can only get a proposal
    onto the board."""
    settings = get_settings()
    with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            "/api/v1/fleet/transfer-orders",
            json={
                "vehicle_id": vehicle_id, "to_hub_id": to_hub_id, "reason": reason,
                "generated_by_agent_key": agent_key,
            },
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


FLEET_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_hub_supply",
        "description": "Fetch current staged-vehicle supply counts per class at a hub.",
        "input_schema": {
            "type": "object",
            "properties": {"hub_id": {"type": "string"}},
            "required": ["hub_id"],
        },
    },
    {
        "name": "get_vehicle",
        "description": "Fetch a vehicle's current status, class, and hub.",
        "input_schema": {
            "type": "object",
            "properties": {"vehicle_id": {"type": "string"}},
            "required": ["vehicle_id"],
        },
    },
    {
        "name": "propose_transfer",
        "description": (
            "Propose moving a vehicle to a different hub. This only creates a PROPOSED "
            "transfer order — it never dispatches. A human must separately assign a "
            "driver/transport before the vehicle actually moves."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "vehicle_id": {"type": "string"},
                "to_hub_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["vehicle_id", "to_hub_id", "reason"],
        },
    },
]


def record_safety_score(tenant_id: str, vehicle_id: str, score: float, reasoning: str, window_days: int = 30) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/fleet/vehicles/{vehicle_id}/safety-scores",
                params={"score": score, "reasoning": reasoning, "window_days": window_days},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_safety_score(score: float, reasoning: str) -> dict[str, Any]:
    return {"score": score, "reasoning": reasoning}


SAFETY_SCORE_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_safety_score",
        "description": "Report a safety score 0-100 (higher is safer) and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["score", "reasoning"],
        },
    },
]


def build_safety_score_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_safety_score": lambda inp: submit_safety_score(inp["score"], inp["reasoning"]),
    }


def record_intake_readiness(tenant_id: str, intake_inspection_id: str, recommendation: str, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/fleet/intake-inspections/{intake_inspection_id}/readiness-assessments",
                json={"recommendation": recommendation, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_readiness_recommendation(recommendation: str, reasoning: str) -> dict[str, Any]:
    return {"recommendation": recommendation, "reasoning": reasoning}


INTAKE_READINESS_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_readiness_recommendation",
        "description": "Report the readiness recommendation (provision_ready, provision_with_flag, or hold_for_review) and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recommendation": {"type": "string", "enum": ["provision_ready", "provision_with_flag", "hold_for_review"]},
                "reasoning": {"type": "string"},
            },
            "required": ["recommendation", "reasoning"],
        },
    },
]


def build_intake_readiness_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_readiness_recommendation": lambda inp: submit_readiness_recommendation(inp["recommendation"], inp["reasoning"]),
    }


def record_telemetry_anomaly(tenant_id: str, geofence_alert_id: str, assessment: str, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/fleet/geofence-alerts/{geofence_alert_id}/anomaly-assessments",
                json={"assessment": assessment, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_anomaly_assessment(assessment: str, reasoning: str) -> dict[str, Any]:
    return {"assessment": assessment, "reasoning": reasoning}


TELEMETRY_ANOMALY_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_anomaly_assessment",
        "description": "Report the anomaly assessment (benign or needs_investigation) and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "assessment": {"type": "string", "enum": ["benign", "needs_investigation"]},
                "reasoning": {"type": "string"},
            },
            "required": ["assessment", "reasoning"],
        },
    },
]


def build_telemetry_anomaly_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_anomaly_assessment": lambda inp: submit_anomaly_assessment(inp["assessment"], inp["reasoning"]),
    }


def record_maintenance_triage(tenant_id: str, alert_ids: list[str], priority_order: list[str], reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/fleet/maintenance-triage-assessments",
                json={"alert_ids": alert_ids, "priority_order": priority_order, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_triage_priority(priority_order: list[str], reasoning: str) -> dict[str, Any]:
    return {"priority_order": priority_order, "reasoning": reasoning}


MAINTENANCE_TRIAGE_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_triage_priority",
        "description": "Report the vehicle IDs in priority order (highest priority first) and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "priority_order": {"type": "array", "items": {"type": "string"}},
                "reasoning": {"type": "string"},
            },
            "required": ["priority_order", "reasoning"],
        },
    },
]


def build_maintenance_triage_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_triage_priority": lambda inp: submit_triage_priority(inp["priority_order"], inp["reasoning"]),
    }


def record_incident_severity(tenant_id: str, incident_id: str, severity: str, summary: str, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/fleet/incidents/{incident_id}/severity-assessments",
                json={"severity": severity, "summary": summary, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_incident_severity(severity: str, summary: str, reasoning: str) -> dict[str, Any]:
    return {"severity": severity, "summary": summary, "reasoning": reasoning}


INCIDENT_SEVERITY_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_incident_severity",
        "description": "Report the severity classification (minor, moderate, or severe), a short summary, and your reasoning. Never assess liability. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": ["minor", "moderate", "severe"]},
                "summary": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["severity", "summary", "reasoning"],
        },
    },
]


def build_incident_severity_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_incident_severity": lambda inp: submit_incident_severity(inp["severity"], inp["summary"], inp["reasoning"]),
    }


def record_valuation_anomaly(tenant_id: str, vehicle_id: str, asset_valuation_id: str, deviation_pct: float, assessment: str, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/fleet/asset-valuations/{asset_valuation_id}/anomaly-assessments",
                json={"vehicle_id": vehicle_id, "deviation_pct": deviation_pct, "assessment": assessment, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_valuation_anomaly(assessment: str, deviation_pct: float, reasoning: str) -> dict[str, Any]:
    return {"assessment": assessment, "deviation_pct": deviation_pct, "reasoning": reasoning}


VALUATION_ANOMALY_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_valuation_anomaly",
        "description": "Report the assessment (normal or flagged), the deviation percentage (negative if market is below book), and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "assessment": {"type": "string", "enum": ["normal", "flagged"]},
                "deviation_pct": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["assessment", "deviation_pct", "reasoning"],
        },
    },
]


def build_valuation_anomaly_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_valuation_anomaly": lambda inp: submit_valuation_anomaly(inp["assessment"], inp["deviation_pct"], inp["reasoning"]),
    }


def create_disposition_channel_recommendation(tenant_id: str, vehicle_id: str, recommended_channel: str, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/fleet/vehicles/{vehicle_id}/disposition-recommendations",
                json={"channel": recommended_channel, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_disposition_channel(recommended_channel: str, reasoning: str) -> dict[str, Any]:
    return {"recommended_channel": recommended_channel, "reasoning": reasoning}


DISPOSITION_CHANNEL_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_disposition_channel",
        "description": "Report the recommended cascade channel (rideshare, local_rental, export, parts, auction, wholesale, or retail_voyqi_auto_group) and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recommended_channel": {"type": "string", "enum": ["rideshare", "local_rental", "export", "parts", "auction", "wholesale", "retail_voyqi_auto_group"]},
                "reasoning": {"type": "string"},
            },
            "required": ["recommended_channel", "reasoning"],
        },
    },
]


def build_disposition_channel_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_disposition_channel": lambda inp: submit_disposition_channel(inp["recommended_channel"], inp["reasoning"]),
    }


def record_covenant_risk(tenant_id: str, facility_id: str, risk_level: str, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/fleet/floorplan-facilities/{facility_id}/covenant-risk-assessments",
                json={"risk_level": risk_level, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_covenant_risk(risk_level: str, reasoning: str) -> dict[str, Any]:
    return {"risk_level": risk_level, "reasoning": reasoning}


COVENANT_RISK_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_covenant_risk",
        "description": "Report the risk level (safe, approaching, or breached) and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "risk_level": {"type": "string", "enum": ["safe", "approaching", "breached"]},
                "reasoning": {"type": "string"},
            },
            "required": ["risk_level", "reasoning"],
        },
    },
]


def build_covenant_risk_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_covenant_risk": lambda inp: submit_covenant_risk(inp["risk_level"], inp["reasoning"]),
    }


def record_fuel_anomaly(tenant_id: str, vehicle_id: str, fuel_log_entry_id: str, assessment: str, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/fleet/fuel-entries/{fuel_log_entry_id}/anomaly-assessments",
                json={"vehicle_id": vehicle_id, "assessment": assessment, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_fuel_anomaly(assessment: str, reasoning: str) -> dict[str, Any]:
    return {"assessment": assessment, "reasoning": reasoning}


FUEL_ANOMALY_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_fuel_anomaly",
        "description": "Report the assessment (normal or flagged) and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "assessment": {"type": "string", "enum": ["normal", "flagged"]},
                "reasoning": {"type": "string"},
            },
            "required": ["assessment", "reasoning"],
        },
    },
]


def build_fuel_anomaly_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_fuel_anomaly": lambda inp: submit_fuel_anomaly(inp["assessment"], inp["reasoning"]),
    }


def record_battery_degradation_forecast(tenant_id: str, vehicle_id: str, degradation_rate_pct_per_day: float, estimated_days_to_threshold: int | None, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/fleet/vehicles/{vehicle_id}/battery-degradation-forecasts",
                json={"degradation_rate_pct_per_day": degradation_rate_pct_per_day, "estimated_days_to_threshold": estimated_days_to_threshold, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_battery_degradation_trend(degradation_rate_pct_per_day: float, estimated_days_to_threshold: int | None, reasoning: str) -> dict[str, Any]:
    return {"degradation_rate_pct_per_day": degradation_rate_pct_per_day, "estimated_days_to_threshold": estimated_days_to_threshold, "reasoning": reasoning}


BATTERY_DEGRADATION_TREND_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_battery_degradation_trend",
        "description": "Report the degradation rate (percentage points per day, negative if declining), estimated days until the alert threshold is crossed (null if flat/improving), and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "degradation_rate_pct_per_day": {"type": "number"},
                "estimated_days_to_threshold": {"type": ["integer", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["degradation_rate_pct_per_day", "estimated_days_to_threshold", "reasoning"],
        },
    },
]


def build_battery_degradation_trend_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_battery_degradation_trend": lambda inp: submit_battery_degradation_trend(inp["degradation_rate_pct_per_day"], inp.get("estimated_days_to_threshold"), inp["reasoning"]),
    }


def record_fleet_optimization(tenant_id: str, vehicle_id: str, recommendation: str, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/fleet/vehicles/{vehicle_id}/fleet-optimization-assessments",
                json={"recommendation": recommendation, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_fleet_optimization(recommendation: str, reasoning: str) -> dict[str, Any]:
    return {"recommendation": recommendation, "reasoning": reasoning}


FLEET_OPTIMIZATION_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_fleet_optimization",
        "description": "Report the recommendation (retain, review_for_disposition, or prioritize_disposition) and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recommendation": {"type": "string", "enum": ["retain", "review_for_disposition", "prioritize_disposition"]},
                "reasoning": {"type": "string"},
            },
            "required": ["recommendation", "reasoning"],
        },
    },
]


def build_fleet_optimization_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_fleet_optimization": lambda inp: submit_fleet_optimization(inp["recommendation"], inp["reasoning"]),
    }


def build_tool_dispatch(tenant_id: str, agent_key: str) -> dict:
    return {
        "get_hub_supply": lambda inp: get_hub_supply(tenant_id, inp["hub_id"]),
        "get_vehicle": lambda inp: get_vehicle(tenant_id, inp["vehicle_id"]),
        "propose_transfer": lambda inp: propose_transfer(
            tenant_id, inp["vehicle_id"], inp["to_hub_id"], inp["reason"], agent_key
        ),
    }
