"""Asks the Marketing Content Agent (ai-orchestration-svc) to draft copy
for a campaign. Service-to-service, so a marketer needs only marketing
permissions to use it — not the AI nucleus's own admin permissions. The
agent writes the draft back through POST /content, so it passes the
same guardrail as anything a person types."""
import httpx

from src.core.config import get_settings


class AIDraftUnavailable(Exception):
    pass


async def generate(tenant_id: str, campaign_id: str, campaign_name: str, channel: str, brief: str) -> dict:
    url = f"{get_settings().ai_orchestration_svc_base_url}/api/v1/ai/marketing/generate-content"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers={"X-Tenant-Id": tenant_id}, json={
                "campaign_id": campaign_id, "campaign_name": campaign_name, "channel": channel, "brief": brief,
            })
    except httpx.HTTPError as exc:
        raise AIDraftUnavailable(f"Marketing Content Agent unreachable ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise AIDraftUnavailable(f"Marketing Content Agent returned HTTP {resp.status_code}")
    return resp.json()


async def compose(tenant_id: str, payload: dict) -> dict:
    """Copy variants from the Marketing Content Agent (AI studio)."""
    url = f"{get_settings().ai_orchestration_svc_base_url}/api/v1/ai/marketing/compose"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers={"X-Tenant-Id": tenant_id}, json=payload)
    except httpx.HTTPError as exc:
        raise AIDraftUnavailable(f"Marketing Content Agent unreachable ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise AIDraftUnavailable(f"Marketing Content Agent returned HTTP {resp.status_code}")
    return resp.json()
