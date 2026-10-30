import uuid

import pytest

from src.core.config import get_settings
from tests.conftest import TENANT_HEADERS

PW = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def internal_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_token", "tok")


def _h(token="tok"):
    return {**TENANT_HEADERS, "X-Internal-Token": token}


async def _register(client, email="op@voyqi.com"):
    uid = str(uuid.uuid4())
    r = await client.post("/api/v1/auth/register", json={"user_id": uid, "email": email, "password": PW}, headers=TENANT_HEADERS)
    assert r.status_code == 201, r.text
    return uid


async def test_requires_internal_token(client, monkeypatch):
    assert (await client.get("/internal/accounts", headers=_h("wrong"))).status_code == 403
    assert (await client.get("/internal/accounts", headers=TENANT_HEADERS)).status_code == 403
    monkeypatch.setattr(get_settings(), "internal_token", "")
    assert (await client.get("/internal/accounts", headers=_h())).status_code == 403


async def test_lockout_shows_and_unlock_clears_it(client):
    uid = await _register(client)
    other = await _register(client, "other@voyqi.com")
    for _ in range(5):
        await client.post("/api/v1/auth/login", json={"email": "op@voyqi.com", "password": "wrong"}, headers=TENANT_HEADERS)
    rows = (await client.get("/internal/accounts", params={"user_ids": [uid]}, headers=_h())).json()
    assert len(rows) == 1 and rows[0]["locked"] is True and rows[0]["failed_login_attempts"] == 5 and rows[0]["locked_until"]
    # the right password is still refused while locked
    assert (await client.post("/api/v1/auth/login", json={"email": "op@voyqi.com", "password": PW}, headers=TENANT_HEADERS)).status_code != 200

    r = await client.post(f"/internal/accounts/{uid}/unlock", headers=_h())
    assert r.status_code == 200 and r.json()["locked"] is False and r.json()["failed_login_attempts"] == 0
    ok = await client.post("/api/v1/auth/login", json={"email": "op@voyqi.com", "password": PW}, headers=TENANT_HEADERS)
    assert ok.status_code == 200
    rows = {x["user_id"]: x for x in (await client.get("/internal/accounts", headers=_h())).json()}
    assert set(rows) == {uid, other} and rows[uid]["last_login_at"] and rows[other]["last_login_at"] is None


async def test_tenant_scoped_and_unknown_user(client):
    uid = await _register(client)
    assert (await client.get("/internal/accounts", headers={"X-Tenant-Id": "someone-else", "X-Internal-Token": "tok"})).json() == []
    assert (await client.post(f"/internal/accounts/{uid}/unlock", headers={"X-Tenant-Id": "someone-else", "X-Internal-Token": "tok"})).status_code == 404
    assert (await client.post(f"/internal/accounts/{uuid.uuid4()}/unlock", headers=_h())).status_code == 404
