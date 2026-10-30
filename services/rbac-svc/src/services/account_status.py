"""Sign-in state (lockouts, last login, MFA) from auth-svc for the Users page.

auth-svc owns credentials; rbac-svc only asks it over /internal/ with the
shared token, so the Super Admin panel sees lockouts and can lift them
without auth-svc exposing admin routes to every signed-in user.
"""
import httpx

from src.core.config import get_settings


class AccountServiceError(Exception):
    pass


def _headers(tenant_id: str) -> dict:
    return {"X-Internal-Token": get_settings().internal_token, "X-Tenant-Id": tenant_id}


async def fetch(tenant_id: str, user_ids: list[str] | None = None) -> dict[str, dict]:
    """{user_id: status}. Raises AccountServiceError when auth-svc can't answer."""
    s = get_settings()
    if not s.internal_token:
        raise AccountServiceError("RBAC_INTERNAL_TOKEN is not set, so sign-in status can't be read from auth-svc.")
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{s.auth_svc_base_url}/internal/accounts", params={"user_ids": user_ids or []}, headers=_headers(tenant_id))
    except httpx.HTTPError as exc:
        raise AccountServiceError(f"auth-svc unreachable: {exc.__class__.__name__}") from exc
    if r.status_code != 200:
        raise AccountServiceError(f"auth-svc answered HTTP {r.status_code}")
    return {row["user_id"]: row for row in r.json()}


async def unlock(tenant_id: str, user_id: str) -> dict:
    s = get_settings()
    if not s.internal_token:
        raise AccountServiceError("RBAC_INTERNAL_TOKEN is not set, so auth-svc can't be asked to unlock.")
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(f"{s.auth_svc_base_url}/internal/accounts/{user_id}/unlock", headers=_headers(tenant_id))
    except httpx.HTTPError as exc:
        raise AccountServiceError(f"auth-svc unreachable: {exc.__class__.__name__}") from exc
    if r.status_code == 404:
        raise LookupError("This user has no sign-in account to unlock.")
    if r.status_code != 200:
        raise AccountServiceError(f"auth-svc answered HTTP {r.status_code}")
    return r.json()
