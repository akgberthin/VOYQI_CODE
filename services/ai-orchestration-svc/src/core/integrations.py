"""Per-tenant Anthropic (and other) credentials set in the super admin
console, loaded from rbac-svc /internal/integrations and refreshed with
the prompt/tier loop. A tenant without its own key uses the env key."""
import logging

import httpx

from src.core.config import get_settings

logger = logging.getLogger(__name__)

# provider -> {tenant_id: config}
_configs: dict[str, dict[str, dict]] = {}


def for_tenant(provider: str, tenant_id: str | None) -> dict | None:
    return _configs.get(provider, {}).get(tenant_id) if tenant_id else None


def set_configs(provider: str, by_tenant: dict[str, dict]) -> None:
    _configs[provider] = dict(by_tenant)


async def refresh(providers: tuple[str, ...] = ("anthropic", "openai")) -> None:
    settings = get_settings()
    if not settings.internal_token:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        for provider in providers:
            try:
                resp = await client.get(f"{settings.rbac_svc_base_url}/internal/integrations/{provider}",
                                        headers={"X-Internal-Token": settings.internal_token})
                resp.raise_for_status()
                set_configs(provider, resp.json())
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("couldn't refresh %s integrations; keeping the last ones: %s", provider, exc)
