"""CORS: only listed origins are echoed back; super admin's list comes from rbac-svc."""
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.core.config import get_settings
from src.main import app
from src.services import cors


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    cors.reset()
    s = get_settings()
    monkeypatch.setattr(s, "cors_allowed_origins", "https://static.voyqi.com")
    monkeypatch.setattr(s, "internal_token", "tok")
    yield
    cors.reset()


@pytest.fixture
def rbac(monkeypatch):
    state = {"origins": ["https://app.voyqi.com"], "calls": 0, "down": False}
    real = httpx.AsyncClient

    def handler(request):
        state["calls"] += 1
        assert request.url.path == "/internal/security/cors-origins" and request.headers["x-internal-token"] == "tok"
        if state["down"]:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"origins": state["origins"]})

    monkeypatch.setattr(cors.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_preflight_for_listed_origin(rbac):
    async with await _client() as c:
        r = await c.options("/api/v1/fleet/vehicles", headers={"Origin": "https://app.voyqi.com", "Access-Control-Request-Method": "POST"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "https://app.voyqi.com" and r.headers["access-control-allow-credentials"] == "true"
    assert "X-Tenant-Id" in r.headers["access-control-allow-headers"]


async def test_unlisted_origin_gets_no_cors_headers(rbac):
    async with await _client() as c:
        r = await c.get("/health", headers={"Origin": "https://evil.example"})
        p = await c.options("/api/v1/fleet/vehicles", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    assert "access-control-allow-origin" not in p.headers


async def test_simple_request_and_static_origin(rbac):
    async with await _client() as c:
        r = await c.get("/health", headers={"Origin": "https://static.voyqi.com"})
    assert r.headers["access-control-allow-origin"] == "https://static.voyqi.com" and r.headers["vary"] == "Origin"


async def test_list_is_cached_and_kept_when_rbac_is_down(rbac, monkeypatch):
    async with await _client() as c:
        await c.get("/health", headers={"Origin": "https://app.voyqi.com"})
        await c.get("/health", headers={"Origin": "https://app.voyqi.com"})
        assert rbac["calls"] == 1
        monkeypatch.setattr(get_settings(), "cors_refresh_seconds", 0)
        rbac["down"] = True
        r = await c.get("/health", headers={"Origin": "https://app.voyqi.com"})
        assert r.headers["access-control-allow-origin"] == "https://app.voyqi.com"
        rbac["down"] = False
        rbac["origins"] = []
        r = await c.get("/health", headers={"Origin": "https://app.voyqi.com"})
        assert "access-control-allow-origin" not in r.headers
