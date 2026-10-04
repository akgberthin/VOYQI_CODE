"""
Rental tool adapters, mirroring dms_tools.py / marketing_tools.py:
real HTTP calls into rental-svc.

The actual supply+demand pricing computation lives in rental-svc's own
pricing_engine.py, not here or in the agent — get_pricing_recommendation
is the single source of truth both the deterministic fallback and a
live Claude tool-use pass call into, so the multiplier math is never
duplicated in two places that could quietly drift apart.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": settings.ai_initiated_header_value}


def get_utilization(tenant_id: str, hub_id: str, rental_class: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.rental_svc_base_url, timeout=10.0) as client:
        resp = client.get(
            f"/api/v1/rental/hubs/{hub_id}/utilization", params={"rental_class": rental_class}, headers=_headers(tenant_id)
        )
        resp.raise_for_status()
        return resp.json()


def get_demand_forecast(tenant_id: str, hub_id: str, rental_class: str) -> dict[str, Any]:
    """The demand-side signal utilization alone cannot provide —
    booking velocity and trend."""
    settings = get_settings()
    with httpx.Client(base_url=settings.rental_svc_base_url, timeout=10.0) as client:
        resp = client.get(
            f"/api/v1/rental/hubs/{hub_id}/demand-forecast", params={"rental_class": rental_class}, headers=_headers(tenant_id)
        )
        resp.raise_for_status()
        return resp.json()


def get_reservation(tenant_id: str, reservation_id: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.rental_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/rental/reservations/{reservation_id}", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def get_pricing_recommendation(tenant_id: str, reservation_id: str) -> dict[str, Any]:
    """The real two-factor supply+demand computation, read-only —
    rental-svc's pricing_engine.recommend_rate does the actual
    multiplier math; this just fetches the result."""
    settings = get_settings()
    with httpx.Client(base_url=settings.rental_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/rental/pricing/reservations/{reservation_id}/recommendation", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def apply_dynamic_rate(tenant_id: str, reservation_id: str, new_daily_rate_usd: float) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.rental_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            f"/api/v1/rental/reservations/{reservation_id}/modify",
            json={"daily_rate_usd": new_daily_rate_usd},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


PRICING_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_utilization",
        "description": "Fetch current booked/available utilization percentage for a hub and rental class — the supply-side signal.",
        "input_schema": {
            "type": "object",
            "properties": {"hub_id": {"type": "string"}, "rental_class": {"type": "string"}},
            "required": ["hub_id", "rental_class"],
        },
    },
    {
        "name": "get_demand_forecast",
        "description": "Fetch booking velocity and trend (accelerating/steady/decelerating) for a hub and rental class — the demand-side signal utilization alone cannot provide. Two hubs at identical utilization can be in genuinely different positions if one is filling fast and the other is flat.",
        "input_schema": {
            "type": "object",
            "properties": {"hub_id": {"type": "string"}, "rental_class": {"type": "string"}},
            "required": ["hub_id", "rental_class"],
        },
    },
    {
        "name": "get_reservation",
        "description": "Fetch a reservation's current rate and status.",
        "input_schema": {
            "type": "object",
            "properties": {"reservation_id": {"type": "string"}},
            "required": ["reservation_id"],
        },
    },
    {
        "name": "get_pricing_recommendation",
        "description": "Fetch rental-svc's own combined supply+demand rate recommendation for a reservation, with the full multiplier breakdown and reasoning. Read-only, safe to call freely.",
        "input_schema": {
            "type": "object",
            "properties": {"reservation_id": {"type": "string"}},
            "required": ["reservation_id"],
        },
    },
    {
        "name": "apply_dynamic_rate",
        "description": "Apply a revised daily rate to a reservation. Only takes effect before checkout.",
        "input_schema": {
            "type": "object",
            "properties": {"reservation_id": {"type": "string"}, "new_daily_rate_usd": {"type": "number"}},
            "required": ["reservation_id", "new_daily_rate_usd"],
        },
    },
]


def build_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_utilization": lambda inp: get_utilization(tenant_id, inp["hub_id"], inp["rental_class"]),
        "get_demand_forecast": lambda inp: get_demand_forecast(tenant_id, inp["hub_id"], inp["rental_class"]),
        "get_reservation": lambda inp: get_reservation(tenant_id, inp["reservation_id"]),
        "get_pricing_recommendation": lambda inp: get_pricing_recommendation(tenant_id, inp["reservation_id"]),
        "apply_dynamic_rate": lambda inp: apply_dynamic_rate(tenant_id, inp["reservation_id"], inp["new_daily_rate_usd"]),
    }
