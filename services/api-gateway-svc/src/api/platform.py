"""Platform introspection for the super admin console: which services
sit behind the gateway, whether each is healthy, and every API each one
exposes (its OpenAPI document, limited to the paths the gateway routes).

Served by the gateway itself, not proxied. Same authentication as any
proxied call, and the "platform" module permission — platform.read for
these GETs, which the super admin role's "*" includes.
"""
import asyncio
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request

from src.core.config import get_settings
from src.core.tokens import TokenInvalidError, decode_access_token
from src.services import authorization
from src.services.revocation import is_revoked
from src.services.routing import ROUTE_TABLE

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])

OPENAPI_TTL_SECONDS = 60
_openapi_cache: dict[str, tuple[float, dict]] = {}


def services() -> dict[str, dict]:
    """One entry per downstream service; a service reached under several
    prefixes (security-compliance-svc, originations-svc) is listed once."""
    out: dict[str, dict] = {}
    for route in ROUTE_TABLE:
        name = urlparse(route.target_base_url).hostname
        entry = out.setdefault(name, {"name": name, "base_url": route.target_base_url, "prefixes": []})
        entry["prefixes"].append(route.prefix)
    return out


async def _require(request: Request, permission: str) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        claims = decode_access_token(token)
    except TokenInvalidError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if await is_revoked(claims):
        raise HTTPException(status_code=401, detail="access token revoked")
    if get_settings().enforce_rbac:
        try:
            granted = await authorization.permissions_for(claims["tenant_id"], claims["user_id"])
        except authorization.AuthorizationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not authorization.is_allowed(granted, permission):
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
    return claims


async def fetch(url: str, timeout: float) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(url)


async def _health(entry: dict) -> dict:
    """status: "up", "down" (refused, erroring) or "timeout" (no answer in
    time — overloaded rather than necessarily dead)."""
    started = time.monotonic()
    try:
        resp = await fetch(f"{entry['base_url']}/health", timeout=5.0)
        status = "up" if resp.status_code == 200 else "down"
        error = None if status == "up" else f"HTTP {resp.status_code}"
    except httpx.TimeoutException as exc:
        status, error = "timeout", type(exc).__name__
    except httpx.HTTPError as exc:
        status, error = "down", type(exc).__name__
    return {**entry, "status": status, "healthy": status == "up", "latency_ms": round((time.monotonic() - started) * 1000), "error": error}


@router.get("/services")
async def list_services(request: Request):
    await _require(request, "platform.read")
    return await asyncio.gather(*(_health(entry) for entry in services().values()))


@router.get("/services/{name}/openapi")
async def service_openapi(name: str, request: Request):
    """The service's OpenAPI document with only the operations a client
    can reach through the gateway (paths under the service's prefixes)."""
    await _require(request, "platform.read")
    entry = services().get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No service named {name}")
    cached = _openapi_cache.get(name)
    if cached and time.monotonic() - cached[0] < OPENAPI_TTL_SECONDS:
        spec = cached[1]
    else:
        try:
            resp = await fetch(f"{entry['base_url']}/openapi.json", timeout=10.0)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"{name} unreachable ({type(exc).__name__})") from exc
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"{name} returned HTTP {resp.status_code} for its API description")
        spec = resp.json()
        _openapi_cache[name] = (time.monotonic(), spec)
    prefixes = tuple(entry["prefixes"])
    routed = {path: ops for path, ops in spec.get("paths", {}).items() if path.startswith(prefixes)}
    return {"service": name, "prefixes": entry["prefixes"], "openapi": {**spec, "paths": routed}}
