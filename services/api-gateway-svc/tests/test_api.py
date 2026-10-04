from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.services import rate_limiter
from tests.conftest import make_test_token


def setup_function():
    rate_limiter.reset_all()


@pytest.mark.asyncio
async def test_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "api-gateway-svc"


@pytest.mark.asyncio
async def test_unauthenticated_request_to_protected_path_returns_401():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/fleet/vehicles")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_authenticated_request_reaches_backend_through_the_gateway():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"vehicles": []}'
    mock_response.headers = {"content-type": "application/json"}

    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    token = make_test_token()
    transport = ASGITransport(app=app)
    with patch("src.services.proxy_engine.httpx.AsyncClient", return_value=mock_client):
        async with AsyncClient(transport=transport, base_url="http://test", cookies={"access_token": token}) as client:
            resp = await client.get("/api/v1/fleet/vehicles")

    assert resp.status_code == 200
    assert resp.json() == {"vehicles": []}


@pytest.mark.asyncio
async def test_unknown_path_returns_404_through_the_full_app():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/totally-made-up-service/whatever")
    assert resp.status_code == 404
