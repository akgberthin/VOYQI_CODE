"""Super admin introspection: service health and each service's routed
APIs, behind the same authentication and a platform permission."""
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.api import platform
from src.main import app
from src.services import authorization
from tests.conftest import make_test_token


@pytest.fixture
def upstream(monkeypatch):
    """Every service healthy except fleet-svc; rbac-svc's API description
    has one routed and one internal path."""
    platform._openapi_cache.clear()
    calls = []

    async def fake_fetch(url, timeout):
        calls.append(url)
        request = httpx.Request("GET", url)
        if url.startswith("http://fleet-svc"):
            raise httpx.ConnectError("down", request=request)
        if url.startswith("http://hrm-svc"):
            raise httpx.ReadTimeout("slow", request=request)
        if url.endswith("/openapi.json"):
            return httpx.Response(200, request=request, json={"openapi": "3.1.0", "paths": {
                "/api/v1/rbac/roles": {"get": {"summary": "List roles"}}, "/internal/metrics": {"get": {}},
            }})
        return httpx.Response(200, request=request, json={"status": "ok"})

    monkeypatch.setattr(platform, "fetch", fake_fetch)
    return calls


async def _get(path, token=True):
    cookies = {"access_token": make_test_token()} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_needs_a_session(upstream):
    assert (await _get("/api/v1/platform/services", token=False)).status_code == 401


@pytest.mark.asyncio
async def test_needs_the_platform_permission(upstream, monkeypatch):
    async def fleet_only(tenant_id, user_id):
        return frozenset({"fleet.*"})

    monkeypatch.setattr(authorization, "permissions_for", fleet_only)
    resp = await _get("/api/v1/platform/services")
    assert resp.status_code == 403 and "platform.read" in resp.text


@pytest.mark.asyncio
async def test_lists_each_service_once_with_health(upstream):
    services = {s["name"]: s for s in (await _get("/api/v1/platform/services")).json()}
    assert len(services) == len({r.target_base_url for r in platform.ROUTE_TABLE})
    assert services["security-compliance-svc"]["prefixes"] == ["/api/v1/identity", "/api/v1/security-compliance"]
    assert services["rbac-svc"]["healthy"] is True
    assert (services["fleet-svc"]["status"], services["fleet-svc"]["error"]) == ("down", "ConnectError")
    assert (services["hrm-svc"]["status"], services["hrm-svc"]["healthy"]) == ("timeout", False)
    assert services["rbac-svc"]["status"] == "up"


@pytest.mark.asyncio
async def test_openapi_is_limited_to_routed_paths_and_cached(upstream):
    first = (await _get("/api/v1/platform/services/rbac-svc/openapi")).json()
    assert list(first["openapi"]["paths"]) == ["/api/v1/rbac/roles"]
    await _get("/api/v1/platform/services/rbac-svc/openapi")
    assert sum(u.endswith("/openapi.json") for u in upstream) == 1


@pytest.mark.asyncio
async def test_unknown_or_unreachable_services(upstream):
    assert (await _get("/api/v1/platform/services/nope/openapi")).status_code == 404
    assert (await _get("/api/v1/platform/services/fleet-svc/openapi")).status_code == 502
