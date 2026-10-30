"""CORS for browser apps on other origins (member app, partner portals).

Allowed origins = GATEWAY_CORS_ALLOWED_ORIGINS (comma-separated, fixed)
plus whatever Super Admin lists under Platform settings → Security, read
from rbac-svc's /internal/security/cors-origins and refreshed every
cors_refresh_seconds. If rbac-svc can't be reached the last known list is
kept (never widened to "*"). Credentials are allowed, so an origin is
only ever echoed back when it is on the list.
"""
import time

import httpx

from src.core.config import get_settings

ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
ALLOW_HEADERS = "Authorization, Content-Type, X-Tenant-Id, X-Requested-With"
MAX_AGE = "600"

_state: dict = {"origins": frozenset(), "fetched": 0.0}


def reset() -> None:
    _state.update(origins=frozenset(), fetched=0.0)


def _static() -> set[str]:
    return {o.strip().rstrip("/").lower() for o in get_settings().cors_allowed_origins.split(",") if o.strip()}


async def _dynamic() -> frozenset[str]:
    s = get_settings()
    now = time.monotonic()
    if _state["fetched"] and now - _state["fetched"] < s.cors_refresh_seconds:
        return _state["origins"]
    if not s.internal_token:
        return _state["origins"]
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{s.rbac_base_url}/internal/security/cors-origins", headers={"X-Internal-Token": s.internal_token})
        if r.status_code == 200:
            _state["origins"] = frozenset(o.lower() for o in r.json().get("origins", []))
    except httpx.HTTPError:
        pass  # keep the last known list
    _state["fetched"] = now
    return _state["origins"]


async def allowed(origin: str | None) -> bool:
    if not origin:
        return False
    o = origin.rstrip("/").lower()
    return o in _static() or o in await _dynamic()


def headers(origin: str, preflight: bool) -> dict[str, str]:
    h = {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Vary": "Origin"}
    if preflight:
        h.update({"Access-Control-Allow-Methods": ALLOW_METHODS, "Access-Control-Allow-Headers": ALLOW_HEADERS, "Access-Control-Max-Age": MAX_AGE})
    return h
