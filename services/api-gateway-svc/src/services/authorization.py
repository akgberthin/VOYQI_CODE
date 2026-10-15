"""Role-based authorization at the gateway — the platform's single
ingress, so the one place every client request can be checked.

Each route prefix is a module ("/api/v1/fleet" -> "fleet"). Reading
(GET/HEAD/OPTIONS) needs "<module>.read", anything else needs
"<module>.write". A user's effective permissions come from rbac-svc
(roles + overrides, deny always wins) and may include wildcards:
"<module>.*" grants both, "*" grants everything. "<module>.self" grants
only the self-service endpoints in self_service.py, on the caller's own
records (members).

Fails closed: if rbac-svc can't be reached, the request is refused,
never let through. Service-to-service calls (the AI nucleus calling
tools) don't pass through the gateway and are unaffected.
"""
import time

import httpx

from src.core.config import get_settings
from src.services import self_service
from src.services.routing import RouteConfig

READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# Self-service paths any authenticated user may reach regardless of role:
# their own session (/auth/*) and their own user record.
SELF_SERVICE_PREFIXES = ("/api/v1/auth/",)


class AuthorizationError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


_cache: dict[tuple[str, str], tuple[float, frozenset[str]]] = {}


def reset_cache() -> None:
    _cache.clear()


def module_for(route: RouteConfig) -> str:
    return route.prefix.rstrip("/").rsplit("/", 1)[-1]


def required_permission(route: RouteConfig, method: str) -> str:
    return f"{module_for(route)}.{'read' if method.upper() in READ_METHODS else 'write'}"


def is_allowed(permissions: frozenset[str], required: str) -> bool:
    module = required.split(".", 1)[0]
    return required in permissions or f"{module}.*" in permissions or "*" in permissions


def is_self_service(path: str, user_id: str) -> bool:
    if path.startswith(SELF_SERVICE_PREFIXES):
        return True
    own_record = f"/api/v1/user/users/{user_id}"
    return path == own_record or path.startswith(own_record + "/")


async def fetch_permissions(tenant_id: str, user_id: str) -> frozenset[str]:
    """Effective permissions from rbac-svc. A user with no platform-user
    record there simply has none."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            resp = await client.get(
                f"{settings.rbac_base_url}/api/v1/rbac/users/{user_id}/effective-permissions",
                headers={"x-tenant-id": tenant_id},
            )
    except httpx.HTTPError as exc:
        raise AuthorizationError(503, "Authorization service unavailable") from exc
    if resp.status_code == 404:
        return frozenset()
    if resp.status_code != 200:
        raise AuthorizationError(503, "Authorization service unavailable")
    return frozenset(resp.json().get("effective_permissions", []))


async def permissions_for(tenant_id: str, user_id: str) -> frozenset[str]:
    ttl = get_settings().permission_cache_seconds
    key = (tenant_id, user_id)
    cached = _cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < ttl:
        return cached[1]
    permissions = await fetch_permissions(tenant_id, user_id)
    _cache[key] = (now, permissions)
    return permissions


async def authorize(claims: dict, route: RouteConfig, method: str, path: str, body: bytes = b"") -> None:
    """Raises AuthorizationError unless the caller may make this request."""
    if not get_settings().enforce_rbac:
        return
    if is_self_service(path, claims["user_id"]):
        return
    required = required_permission(route, method)
    permissions = await permissions_for(claims["tenant_id"], claims["user_id"])
    if is_allowed(permissions, required):
        return
    if f"{module_for(route)}.self" in permissions:
        try:
            await self_service.check(claims, method, path, body)
        except self_service.NotOwnerError as exc:
            raise AuthorizationError(403, str(exc)) from exc
        return
    raise AuthorizationError(403, f"Missing permission: {required}")
