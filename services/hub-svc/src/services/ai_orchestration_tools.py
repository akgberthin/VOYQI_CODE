"""
Outbound calls from hub-svc to ai-orchestration-svc — the same
pass-through pattern used by dms-svc's ai_orchestration_tools.py.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def request_hub_rebalancing(tenant_id: str, source_hub_id: str, reason: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/fleet/evaluate-hub-rebalancing",
                json={"source_hub_id": source_hub_id, "reason": reason},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None
