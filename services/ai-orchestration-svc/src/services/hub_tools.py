"""
Hub tool adapters. Two different agents use these for two different
purposes:

- Fleet Intelligence Agent reads the real network utilization snapshot
  (with geo coordinates) to reason about hub rebalancing — genuine
  judgment about which hub should supply or receive vehicles, not just
  "nearest one."
- Compliance/Audit Agent reads a card's real tap history to judge
  whether a flagged geographic anomaly is a member's first or part of
  a repeat pattern, and whether the two hubs involved are ones the
  member visits normally — context a fixed risk score alone can't see.
"""
import math
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": settings.ai_initiated_header_value}


def get_network_snapshot(tenant_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hub_svc_base_url, timeout=10.0) as client:
            resp = client.get("/api/v1/hub/network-snapshot", headers=_headers(tenant_id))
            resp.raise_for_status()
            return {"hubs": resp.json()}
    except httpx.HTTPError:
        return {"hubs": []}


def get_card_tap_history(tenant_id: str, card_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hub_svc_base_url, timeout=10.0) as client:
            resp = client.get(f"/api/v1/hub/cards/{card_id}/tap-history", headers=_headers(tenant_id))
            resp.raise_for_status()
            return {"taps": resp.json()}
    except httpx.HTTPError:
        return {"taps": []}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def submit_rebalancing_recommendation(candidate_hub_id: str | None, reasoning: str) -> dict[str, Any]:
    """Explicitly accepts a null candidate: 'no good candidate exists'
    is a structured answer, not a failure to answer — the same
    discipline as the Pricing Intelligence Agent's max-bid capability."""
    return {"candidate_hub_id": candidate_hub_id, "reasoning": reasoning}


def submit_anomaly_assessment(severity_adjustment: str, reasoning: str) -> dict[str, Any]:
    """severity_adjustment is one of 'escalate', 'unchanged', 'de-escalate'
    — the agent's judgment on top of the fixed deterministic risk score,
    grounded in the member's real tap history, not a re-roll of the
    score itself."""
    return {"severity_adjustment": severity_adjustment, "reasoning": reasoning}


NETWORK_SNAPSHOT_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_network_snapshot",
        "description": "Fetch the real hub network utilization snapshot (name, type, capacity, scheduled count, utilization %, lat/long) for every active hub.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_rebalancing_recommendation",
        "description": (
            "Report which hub (by id) should supply or receive vehicles, with your reasoning, "
            "or pass candidate_hub_id=null if no good candidate exists. Always call this to conclude."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate_hub_id": {"type": ["string", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["candidate_hub_id", "reasoning"],
        },
    },
]


def build_rebalancing_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_network_snapshot": lambda inp: get_network_snapshot(tenant_id),
        "submit_rebalancing_recommendation": lambda inp: submit_rebalancing_recommendation(inp.get("candidate_hub_id"), inp["reasoning"]),
    }


CARD_TAP_HISTORY_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_card_tap_history",
        "description": "Fetch the real tap history (hub, timestamp, anomaly flag) for a VOYQI Card, most recent first.",
        "input_schema": {
            "type": "object",
            "properties": {"card_id": {"type": "string"}},
            "required": ["card_id"],
        },
    },
]


def build_card_tap_history_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_card_tap_history": lambda inp: get_card_tap_history(tenant_id, inp["card_id"]),
    }
