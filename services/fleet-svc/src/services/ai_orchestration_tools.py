"""
Outbound calls from fleet-svc to ai-orchestration-svc — same
pass-through pattern used by every other service's
ai_orchestration_tools.py. fleet-svc's existing staging flow works the
other direction (the agent reaches back in via fleet_tools.py), but a
synchronous "assess now" trigger for safety scoring is genuinely
useful for a fleet ops UI, matching the pattern established in
org-svc and hrm-svc.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def request_safety_score(tenant_id: str, vehicle_id: str, event_counts: dict[str, int], window_days: int = 30) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-safety-score",
                json={"vehicle_id": vehicle_id, "event_counts": event_counts, "window_days": window_days},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_intake_readiness(tenant_id: str, intake_inspection_id: str, passed: bool, notes: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-intake-readiness",
                json={"intake_inspection_id": intake_inspection_id, "passed": passed, "notes": notes},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_telemetry_anomaly(tenant_id: str, geofence_alert_id: str, distance_miles: float) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-telemetry-anomaly",
                json={"geofence_alert_id": geofence_alert_id, "distance_miles": distance_miles},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_maintenance_triage(tenant_id: str, alerts: list[dict]) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-maintenance-triage",
                json={"alerts": alerts},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_incident_severity(tenant_id: str, incident_id: str, incident_type: str, description: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-incident-severity",
                json={"incident_id": incident_id, "incident_type": incident_type, "description": description},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_valuation_anomaly(tenant_id: str, vehicle_id: str, asset_valuation_id: str, book_value_usd: float, market_value_usd: float) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-valuation-anomaly",
                json={"vehicle_id": vehicle_id, "asset_valuation_id": asset_valuation_id, "book_value_usd": book_value_usd, "market_value_usd": market_value_usd},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_disposition_channel(tenant_id: str, vehicle_id: str, odometer_miles: int, vehicle_class: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-disposition-channel",
                json={"vehicle_id": vehicle_id, "odometer_miles": odometer_miles, "vehicle_class": vehicle_class},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_covenant_risk(tenant_id: str, facility_id: str, utilization_pct: float, max_utilization_pct: float) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-covenant-risk",
                json={"facility_id": facility_id, "utilization_pct": utilization_pct, "max_utilization_pct": max_utilization_pct},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_fuel_anomaly(tenant_id: str, vehicle_id: str, fuel_log_entry_id: str, energy_type: str, implied_efficiency: float | None, cost_per_unit_usd: float | None) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-fuel-anomaly",
                json={"vehicle_id": vehicle_id, "fuel_log_entry_id": fuel_log_entry_id, "energy_type": energy_type, "implied_efficiency": implied_efficiency, "cost_per_unit_usd": cost_per_unit_usd},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_battery_degradation_trend(tenant_id: str, vehicle_id: str, first_soh_pct: float, latest_soh_pct: float, days_elapsed: float, threshold_pct: float = 80.0) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-battery-degradation-trend",
                json={"vehicle_id": vehicle_id, "first_soh_pct": first_soh_pct, "latest_soh_pct": latest_soh_pct, "days_elapsed": days_elapsed, "threshold_pct": threshold_pct},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_fleet_optimization(tenant_id: str, signals: dict) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-fleet-optimization",
                json=signals,
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None
