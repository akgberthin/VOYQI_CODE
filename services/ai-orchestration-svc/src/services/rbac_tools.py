"""
RBAC tool adapters for the Compliance/Audit Agent.

Each function here is exposed to Claude as an Anthropic tool-use schema
and, when called, makes a real HTTP request to rbac-svc — the agent
never touches rbac-svc's database directly and rbac-svc never knows
whether a caller is a human operator or this AI (beyond the
X-AI-Initiated header, which exists precisely so audit logs can tell
the two apart).
"""
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {
        "X-Tenant-Id": tenant_id,
        "X-AI-Initiated": settings.ai_initiated_header_value,
    }


def get_user_rbac_context(tenant_id: str, user_id: str) -> dict[str, Any]:
    """Read-only: fetches the user's active roles and active permission
    overrides from rbac-svc. This is how the agent builds context before
    deciding anything."""
    rbac_base = get_settings().rbac_svc_base_url
    with httpx.Client(base_url=rbac_base, timeout=10.0) as client:
        resp = client.get(f"/api/v1/rbac/users/{user_id}/context", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def revoke_permission_override(tenant_id: str, override_id: str, reason: str) -> dict[str, Any]:
    """Acts: revokes a permission override the agent has assessed as high
    risk. This is the agent taking action, not just recommending one —
    used only for auto-executable (below-threshold) decisions; anything
    at or above the agent's human_approval_threshold goes through AI-002
    review instead of calling this directly."""
    rbac_base = get_settings().rbac_svc_base_url
    with httpx.Client(base_url=rbac_base, timeout=10.0) as client:
        resp = client.post(
            f"/api/v1/rbac/permission-overrides/{override_id}/revoke",
            json={"revoked_by": "ai-orchestration-svc:compliance_audit_agent", "reason": reason},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


RBAC_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_user_rbac_context",
        "description": (
            "Fetch a user's currently active roles and active permission "
            "overrides from rbac-svc, including whether each override touches "
            "a sensitive permission and who granted it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
        },
    },
    {
        "name": "revoke_permission_override",
        "description": (
            "Revoke a specific permission override in rbac-svc. Only call this "
            "for overrides you have assessed as low enough risk to act on "
            "autonomously; anything high-risk should be surfaced as a "
            "recommendation for human review instead of calling this tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "override_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["override_id", "reason"],
        },
    },
]


def build_tool_dispatch(tenant_id: str) -> dict:
    """Binds tenant_id into each tool function so the agent's calls stay
    tenant-scoped without Claude ever needing to know or pass a tenant id
    itself — one less thing that could be spoofed by a prompt."""
    return {
        "get_user_rbac_context": lambda inp: get_user_rbac_context(tenant_id, inp["user_id"]),
        "revoke_permission_override": lambda inp: revoke_permission_override(
            tenant_id, inp["override_id"], inp["reason"]
        ),
    }
