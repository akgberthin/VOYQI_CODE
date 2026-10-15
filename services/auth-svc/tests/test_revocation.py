"""Early revocation of stateless access tokens, recorded in Redis for the
gateway to enforce."""
from unittest.mock import AsyncMock, patch

import jwt
import pytest

from src.core import revocation
from src.core.config import get_settings
from tests.conftest import TENANT_HEADERS
from tests.test_auth_api import _register_and_get_payload


class FakeRedis:
    def __init__(self):
        self.store: dict[str, tuple[object, int]] = {}

    async def setex(self, key, ttl, value):
        self.store[key] = (value, ttl)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(revocation, "_redis", lambda: fake)
    return fake


async def _login(client):
    await _register_and_get_payload(client)
    resp = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "correct-horse-battery-staple"}, headers=TENANT_HEADERS)
    return resp.cookies.get("access_token")


async def test_access_tokens_carry_a_unique_jti(client):
    token = await _login(client)
    claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    assert len(claims["jti"]) == 32


async def test_logout_revokes_the_access_token_until_it_expires(client, redis):
    token = await _login(client)
    claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    client.cookies.set("access_token", token)
    resp = await client.post("/api/v1/auth/logout", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    value, ttl = redis.store[f"revoked:jti:{claims['jti']}"]
    assert 0 < ttl <= get_settings().access_token_ttl_minutes * 60


async def test_password_reset_revokes_every_existing_session(client, redis):
    await _register_and_get_payload(client)
    me = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "correct-horse-battery-staple"}, headers=TENANT_HEADERS)
    cred_id = jwt.decode(me.cookies.get("access_token"), get_settings().jwt_secret, algorithms=["HS256"])["sub"]
    with patch("src.services.notification_client.send_password_reset_email", new_callable=AsyncMock) as mock_send:
        await client.post("/api/v1/auth/password-reset/request", json={"email": "member@voyqi.com"}, headers=TENANT_HEADERS)
        raw_token = mock_send.call_args.kwargs["raw_token"]
    await client.post("/api/v1/auth/password-reset/confirm", json={"token": raw_token, "new_password": "brand-new-password-999"}, headers=TENANT_HEADERS)

    assert f"revoked:cred:{TENANT_HEADERS['X-Tenant-Id']}:{cred_id}" in redis.store


async def test_revocation_is_a_no_op_without_redis(monkeypatch):
    monkeypatch.setattr(revocation, "_redis", lambda: None)
    await revocation.revoke_token("abc", None)
    await revocation.revoke_all_for_credential("t", "c")


async def test_redis_failure_never_blocks_logout(monkeypatch):
    class Broken:
        async def setex(self, *a):
            raise ConnectionError("redis down")

    monkeypatch.setattr(revocation, "_redis", lambda: Broken())
    await revocation.revoke_token("abc", None)
