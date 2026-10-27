"""Calls into the SEO agents (ai-orchestration-svc): the autopilot cycle
and the task-intake agent. Service-to-service, so marketers need only
marketing permissions."""
import httpx

from src.core.config import get_settings
from src.seo.common import SeoError


class AgentsUnavailable(SeoError):
    def __init__(self, detail: str):
        super().__init__(503, detail, "agents_unavailable")


async def _post(tenant_id: str, path: str, timeout: float) -> dict:
    url = f"{get_settings().ai_orchestration_svc_base_url}/api/v1/ai/seo{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers={"X-Tenant-Id": tenant_id})
    except httpx.HTTPError as exc:
        raise AgentsUnavailable(f"the SEO agents are unreachable ({type(exc).__name__})") from exc
    if resp.status_code >= 400:
        raise AgentsUnavailable(f"the SEO agents answered HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


async def run_cycle(tenant_id: str, site_id: str) -> dict:
    return await _post(tenant_id, f"/sites/{site_id}/cycle", get_settings().seo_cycle_timeout_seconds)


async def intake(tenant_id: str, request_id: str) -> dict:
    return await _post(tenant_id, f"/requests/{request_id}/intake", 60.0)


async def agents(tenant_id: str) -> list[dict]:
    url = f"{get_settings().ai_orchestration_svc_base_url}/api/v1/ai/seo/agents"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"X-Tenant-Id": tenant_id})
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AgentsUnavailable(f"the SEO agents are unreachable ({type(exc).__name__})") from exc
