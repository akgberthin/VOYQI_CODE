"""
identity-svc tool adapters, mirroring rbac_tools.py's pattern: real HTTP
calls into identity-svc, exposed both as Anthropic tool schemas for the
agent's own reasoning loop and as plain functions the AI-002 approval
path calls directly to execute a human's decision.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": settings.ai_initiated_header_value}


def end_break_glass_session(tenant_id: str, session_id: str, reason: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.identity_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            f"/api/v1/identity/break-glass/{session_id}/end",
            json={"ended_by": "ai-orchestration-svc:compliance_audit_agent", "reason": reason},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


def end_impersonation_session(tenant_id: str, session_id: str, reason: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.identity_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            f"/api/v1/identity/impersonation/{session_id}/end",
            json={"ended_by": "ai-orchestration-svc:compliance_audit_agent", "reason": reason},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()
