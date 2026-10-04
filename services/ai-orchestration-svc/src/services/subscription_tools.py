"""
Subscription tool adapters, mirroring rbac_tools.py / marketing_tools.py
/ fleet_tools.py: real HTTP calls into subscription-svc.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": settings.ai_initiated_header_value}


def get_member_profile(tenant_id: str, member_profile_id: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.subscription_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/subscription/members/{member_profile_id}", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def get_gu_ledger(tenant_id: str, member_profile_id: str) -> list[dict[str, Any]]:
    settings = get_settings()
    with httpx.Client(base_url=settings.subscription_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/subscription/members/{member_profile_id}/gu-ledger", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def award_retention_gu_bonus(tenant_id: str, member_profile_id: str, amount: int) -> dict[str, Any]:
    """Fully autonomous: the terminal action for a proactive churn-risk
    intervention. This is deliberately a different code path from the
    winback flow — winback only ever fires once a member has already
    initiated a cancellation and returns an offer for the MEMBER to
    accept or decline (see member_experience_agent.evaluate_winback);
    this function acts before that point, on the platform's own
    initiative, so there's no member consent step to preserve."""
    settings = get_settings()
    with httpx.Client(base_url=settings.subscription_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            "/api/v1/subscription/gu/earn",
            json={"member_profile_id": member_profile_id, "trigger": "retention_save", "amount": amount},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


def list_members(tenant_id: str, mobility_class: str | None = None) -> list[dict[str, Any]]:
    """No pagination on subscription-svc's side yet — fine for a batch
    sweep at this platform's current scale, but worth revisiting before
    this member count grows large enough that one unpaginated fetch
    becomes the actual bottleneck."""
    settings = get_settings()
    params = {"mobility_class": mobility_class} if mobility_class else {}
    with httpx.Client(base_url=settings.subscription_svc_base_url, timeout=15.0) as client:
        resp = client.get("/api/v1/subscription/members", params=params, headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def get_pause_count(tenant_id: str, member_profile_id: str, window_days: int = 365) -> int:
    settings = get_settings()
    with httpx.Client(base_url=settings.subscription_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/subscription/members/{member_profile_id}/pause-count", params={"window_days": window_days}, headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()["pause_count"]


def get_upgrade_pattern(tenant_id: str, member_profile_id: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.subscription_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/subscription/members/{member_profile_id}/upgrade-pattern", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def create_upgrade_case(
    tenant_id: str, member_profile_id: str, subscription_id: str, current_class: str, recommended_class: str, request_count: int, reasoning: str,
) -> dict[str, Any]:
    """Fully autonomous — detecting the pattern and offering the case
    never touches billing. Accepting it (via subscription-svc's own
    /upgrade-cases/{id}/accept) is always a separate, member-confirmed
    action this function never calls."""
    settings = get_settings()
    with httpx.Client(base_url=settings.subscription_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            "/api/v1/subscription/upgrade-cases",
            json={
                "member_profile_id": member_profile_id, "subscription_id": subscription_id, "current_class": current_class,
                "recommended_class": recommended_class, "above_class_request_count": request_count, "reasoning": reasoning,
            },
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


def get_deposit_hold(tenant_id: str, hold_id: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.subscription_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/subscription/deposit-holds/{hold_id}", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


SUBSCRIPTION_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_member_profile",
        "description": "Fetch a member's subscription profile: class, service level, GU balance, LTV.",
        "input_schema": {
            "type": "object",
            "properties": {"member_profile_id": {"type": "string"}},
            "required": ["member_profile_id"],
        },
    },
    {
        "name": "get_gu_ledger",
        "description": "Fetch a member's full Growth Unit earn/redeem history.",
        "input_schema": {
            "type": "object",
            "properties": {"member_profile_id": {"type": "string"}},
            "required": ["member_profile_id"],
        },
    },
]


def build_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_member_profile": lambda inp: get_member_profile(tenant_id, inp["member_profile_id"]),
        "get_gu_ledger": lambda inp: get_gu_ledger(tenant_id, inp["member_profile_id"]),
    }
