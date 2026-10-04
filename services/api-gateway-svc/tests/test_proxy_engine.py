import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx as httpx_module
import pytest

from src.services import rate_limiter
from src.services.proxy_engine import GatewayError, handle_request
from tests.conftest import make_test_token


def setup_function():
    rate_limiter.reset_all()


def _mock_upstream_response(status_code=200, content=b'{"ok": true}', headers=None):
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.content = content
    mock_response.headers = headers or {"content-type": "application/json"}
    return mock_response


def _mock_client(response=None, side_effect=None):
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=response, side_effect=side_effect)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.mark.asyncio
async def test_unknown_path_raises_404():
    with pytest.raises(GatewayError) as exc_info:
        await handle_request(method="GET", path="/api/v1/nonexistent", query_string="", headers={}, body=b"", access_token_cookie=None, client_host="1.2.3.4")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_authenticated_path_without_token_raises_401():
    with pytest.raises(GatewayError) as exc_info:
        await handle_request(method="GET", path="/api/v1/auth/me", query_string="", headers={}, body=b"", access_token_cookie=None, client_host="1.2.3.4")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_raises_401():
    token = make_test_token(expired=True)
    with pytest.raises(GatewayError) as exc_info:
        await handle_request(method="GET", path="/api/v1/auth/me", query_string="", headers={}, body=b"", access_token_cookie=token, client_host="1.2.3.4")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_public_path_forwards_without_a_token():
    mock_client = _mock_client(response=_mock_upstream_response())
    with patch("src.services.proxy_engine.httpx.AsyncClient", return_value=mock_client):
        result = await handle_request(
            method="POST", path="/api/v1/auth/login", query_string="", headers={"x-tenant-id": "voyqi-holdings"},
            body=b'{"email":"a@voyqi.com"}', access_token_cookie=None, client_host="1.2.3.4",
        )
    assert result.status_code == 200
    forwarded_url = mock_client.request.call_args.args[1]
    assert forwarded_url == "http://auth-svc:8024/api/v1/auth/login"


@pytest.mark.asyncio
async def test_authenticated_request_forwards_tenant_from_verified_token_not_client_header():
    """The core security property: even if a client tries to claim a
    DIFFERENT tenant via the X-Tenant-Id header, the gateway must
    override it with whatever the verified JWT actually says."""
    real_tenant = "voyqi-holdings"
    token = make_test_token(tenant_id=real_tenant)
    mock_client = _mock_client(response=_mock_upstream_response())

    with patch("src.services.proxy_engine.httpx.AsyncClient", return_value=mock_client):
        await handle_request(
            method="GET", path="/api/v1/fleet/vehicles", query_string="", headers={"x-tenant-id": "attacker-controlled-other-tenant"},
            body=b"", access_token_cookie=token, client_host="1.2.3.4",
        )

    forwarded_headers = mock_client.request.call_args.kwargs["headers"]
    assert forwarded_headers["x-tenant-id"] == real_tenant  # NOT "attacker-controlled-other-tenant"


@pytest.mark.asyncio
async def test_authenticated_request_injects_user_id_header_from_token():
    user_id = uuid.uuid4()
    token = make_test_token(user_id=user_id)
    mock_client = _mock_client(response=_mock_upstream_response())

    with patch("src.services.proxy_engine.httpx.AsyncClient", return_value=mock_client):
        await handle_request(method="GET", path="/api/v1/fleet/vehicles", query_string="", headers={}, body=b"", access_token_cookie=token, client_host="1.2.3.4")

    forwarded_headers = mock_client.request.call_args.kwargs["headers"]
    assert forwarded_headers["x-user-id"] == str(user_id)


@pytest.mark.asyncio
async def test_upstream_unreachable_raises_502():
    mock_client = _mock_client(side_effect=httpx_module.ConnectError("down"))
    token = make_test_token()
    with patch("src.services.proxy_engine.httpx.AsyncClient", return_value=mock_client), pytest.raises(GatewayError) as exc_info:
        await handle_request(method="GET", path="/api/v1/fleet/vehicles", query_string="", headers={}, body=b"", access_token_cookie=token, client_host="1.2.3.4")
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_rate_limit_exceeded_raises_429():
    from src.core.config import get_settings
    settings = get_settings()
    mock_client = _mock_client(response=_mock_upstream_response())
    token = make_test_token(tenant_id="rate-limit-test-tenant")

    with patch("src.services.proxy_engine.httpx.AsyncClient", return_value=mock_client), \
         patch.object(settings, "rate_limit_requests_per_window", 2):
        for _ in range(2):
            await handle_request(method="GET", path="/api/v1/fleet/vehicles", query_string="", headers={}, body=b"", access_token_cookie=token, client_host="1.2.3.4")
        with pytest.raises(GatewayError) as exc_info:
            await handle_request(method="GET", path="/api/v1/fleet/vehicles", query_string="", headers={}, body=b"", access_token_cookie=token, client_host="1.2.3.4")
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_hop_by_hop_headers_are_stripped_before_forwarding():
    mock_client = _mock_client(response=_mock_upstream_response())
    with patch("src.services.proxy_engine.httpx.AsyncClient", return_value=mock_client):
        await handle_request(
            method="POST", path="/api/v1/auth/login", query_string="", headers={"host": "gateway-internal", "content-length": "123", "x-tenant-id": "voyqi-holdings"},
            body=b"{}", access_token_cookie=None, client_host="1.2.3.4",
        )
    forwarded_headers = mock_client.request.call_args.kwargs["headers"]
    assert "host" not in forwarded_headers
    assert "content-length" not in forwarded_headers
