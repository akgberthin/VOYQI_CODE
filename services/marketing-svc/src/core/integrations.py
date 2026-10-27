"""Per-tenant third-party credentials, as set in the super admin console
(rbac-svc /internal/integrations), cached briefly. None when the tenant
hasn't configured the provider, rbac-svc is unreachable, or no internal
token is set — callers then fall back to env settings or console mode."""
import logging
import time

import httpx

from src.core.config import get_settings

logger = logging.getLogger(__name__)
_cache: dict[tuple[str, str], tuple[float, dict | None]] = {}


def clear() -> None:
    _cache.clear()


async def fetch(tenant_id: str, provider: str) -> dict | None:
    settings = get_settings()
    if not settings.internal_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.rbac_base_url}/internal/integrations/{provider}",
                                    params={"tenant_id": tenant_id}, headers={"X-Internal-Token": settings.internal_token})
        resp.raise_for_status()
        return resp.json().get(tenant_id)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("integration lookup %s/%s failed: %s", tenant_id, provider, exc)
        return None


async def get(tenant_id: str | None, provider: str) -> dict | None:
    if not tenant_id:
        return None
    key = (tenant_id, provider)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < get_settings().integrations_cache_seconds:
        return hit[1]
    config = await fetch(tenant_id, provider)
    _cache[key] = (time.monotonic(), config)
    return config
