"""What every adapter shares: credentials from the super admin's
encrypted store, errors the API understands, and one HTTP helper."""
from typing import Any

import httpx

from src.core import integrations as tenant_integrations
from src.seo.common import SeoError
from src.seo.constants import INTEGRATION_KINDS, PROVIDER_CREDENTIALS

TIMEOUT = 30.0


class ProviderNotConfigured(SeoError):
    def __init__(self, detail: str, **extra: Any):
        super().__init__(503, detail, "provider_not_configured", **extra)


class ProviderError(SeoError):
    """The provider answered, but not with what was asked for."""

    def __init__(self, detail: str, **extra: Any):
        super().__init__(502, detail, "provider_error", **extra)


def credential_provider(integ: Any) -> str | None:
    """The super admin integration whose credentials this provider uses:
    credential_ref 'rbac://<provider>' when set, else the provider's
    default."""
    ref = integ.credential_ref or ""
    if ref.startswith("rbac://"):
        return ref.removeprefix("rbac://")
    return PROVIDER_CREDENTIALS.get(integ.provider or "")


async def credentials(tenant_id: str, provider: str | None, required: bool = True) -> dict:
    if not provider:
        return {}
    creds = await tenant_integrations.get(tenant_id, provider)
    if creds is None and required:
        raise ProviderNotConfigured(
            f"the '{provider}' credentials are not set up: add them in the super admin console under Integrations",
            credential=provider)
    return creds or {}


def require_adapter(kind: str, provider: str | None) -> None:
    providers = INTEGRATION_KINDS[kind][2]
    if not provider or provider not in providers:
        raise ProviderNotConfigured(
            f"no '{provider or 'unconfigured'}' adapter is registered for the '{kind}' integration in this deployment",
            kind=kind, provider=provider)


async def request(method: str, url: str, *, what: str, timeout: float = TIMEOUT, **kwargs: Any) -> httpx.Response:
    """One call to a provider; transport failures and error statuses
    become ProviderError naming the provider."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=kwargs.pop("follow_redirects", True)) as client:
            resp = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ProviderError(f"could not reach {what} ({type(exc).__name__})") from exc
    if resp.status_code >= 400:
        try:
            body = resp.json()
            message = body.get("error", body.get("message", body.get("status_message", "")))
            if isinstance(message, dict):
                message = message.get("message", "")
        except ValueError:
            message = resp.text[:200]
        raise ProviderError(f"{what} answered HTTP {resp.status_code}: {message or 'request refused'}")
    return resp
