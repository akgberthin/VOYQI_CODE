"""Security policy: validation, required-MFA enforcement, CORS origins for the gateway."""
import uuid

import pytest

from src.services import account_status

H = {"X-Tenant-Id": "voyqi-demo"}
URL = "/api/v1/rbac/security-policy"


@pytest.fixture
def auth(monkeypatch):
    state = {"accounts": {}, "down": False}

    async def fetch(tenant_id, user_ids=None):
        if state["down"]:
            raise account_status.AccountServiceError("auth-svc unreachable: ConnectError")
        return {k: v for k, v in state["accounts"].items() if not user_ids or k in user_ids}

    monkeypatch.setattr(account_status, "fetch", fetch)
    return state


async def _user_with_role(client, email="ops@voyqi.com"):
    u = (await client.post("/api/v1/rbac/users", json={"email": email, "display_name": email}, headers=H)).json()
    role = (await client.post("/api/v1/rbac/roles", json={"key": f"r{uuid.uuid4().hex[:6]}", "name": "r", "permission_keys": ["fleet.read"]}, headers=H)).json()
    await client.post("/api/v1/rbac/role-assignments", json={"user_id": u["id"], "role_id": role["id"], "assigned_by": "admin"}, headers=H)
    return u


async def test_defaults_and_fixed_controls(client):
    body = (await client.get(URL, headers=H)).json()
    assert body["require_mfa_for_operators"] is False and body["cors_allowed_origins"] == []
    assert any(e["key"] == "deny_wins" for e in body["enforced"])


async def test_origins_are_validated_and_normalised(client, auth):
    r = await client.put(URL, json={"cors_allowed_origins": ["https://App.voyqi.com/", "https://app.voyqi.com", "http://localhost:3000"], "updated_by": "admin"}, headers=H)
    assert r.status_code == 200 and r.json()["cors_allowed_origins"] == ["https://app.voyqi.com", "http://localhost:3000"]
    for bad in (["*"], ["https://*.voyqi.com"], ["http://app.voyqi.com"], ["https://app.voyqi.com/path"], ["ftp://x.com"], ["voyqi.com"]):
        assert (await client.put(URL, json={"cors_allowed_origins": bad, "updated_by": "admin"}, headers=H)).status_code == 422, bad
    # the generic settings endpoint can't bypass validation
    assert (await client.post("/api/v1/rbac/settings", json={"category": "security", "key": "policy", "value": {"cors_allowed_origins": ["*"]}}, headers=H)).status_code == 422
    audit = (await client.get("/api/v1/rbac/audit-log", headers=H)).json()
    assert any(a["action"] == "security_policy.updated" for a in audit)


async def test_internal_origins_union_needs_token(client, auth):
    await client.put(URL, json={"cors_allowed_origins": ["https://a.voyqi.com"], "updated_by": "admin"}, headers=H)
    await client.put(URL, json={"cors_allowed_origins": ["https://b.voyqi.com"], "updated_by": "admin"}, headers={"X-Tenant-Id": "other"})
    assert (await client.get("/internal/security/cors-origins")).status_code == 403
    r = await client.get("/internal/security/cors-origins", headers={"X-Internal-Token": "test-internal-token"})
    assert r.json() == {"origins": ["https://a.voyqi.com", "https://b.voyqi.com"]}


async def test_required_mfa_removes_access_until_enrolled(client, auth):
    admin = await _user_with_role(client, "admin@voyqi.com")
    ops = await _user_with_role(client, "ops@voyqi.com")
    auth["accounts"][admin["id"]] = {"user_id": admin["id"], "mfa_enabled": False}
    auth["accounts"][ops["id"]] = {"user_id": ops["id"], "mfa_enabled": False}

    # can't turn it on while your own account has no MFA
    r = await client.put(URL, json={"require_mfa_for_operators": True, "updated_by": "admin", "actor_user_id": admin["id"]}, headers=H)
    assert r.status_code == 409 and "own account" in r.json()["detail"]
    assert (await client.put(URL, json={"require_mfa_for_operators": True, "updated_by": "admin"}, headers=H)).status_code == 422

    auth["accounts"][admin["id"]]["mfa_enabled"] = True
    r = await client.put(URL, json={"require_mfa_for_operators": True, "updated_by": "admin", "actor_user_id": admin["id"]}, headers=H)
    assert r.status_code == 200 and r.json()["require_mfa_for_operators"] is True

    eff = lambda u: client.get(f"/api/v1/rbac/users/{u['id']}/effective-permissions", headers=H)  # noqa: E731
    body = (await eff(ops)).json()
    assert body["effective_permissions"] == [] and body["mfa_required"] is True
    assert (await eff(admin)).json()["effective_permissions"] == ["fleet.read"]
    auth["accounts"][ops["id"]]["mfa_enabled"] = True
    assert (await eff(ops)).json()["effective_permissions"] == ["fleet.read"]

    # fails closed when auth-svc can't answer
    auth["down"] = True
    assert (await eff(ops)).status_code == 503
    # users without a sign-in account (service identities) are unaffected
    auth["down"] = False
    svc = await _user_with_role(client, "svc@voyqi.com")
    assert (await eff(svc)).json()["effective_permissions"] == ["fleet.read"]


async def test_policy_off_never_calls_auth(client, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("auth-svc should not be asked")
    monkeypatch.setattr(account_status, "fetch", boom)
    u = await _user_with_role(client)
    assert (await client.get(f"/api/v1/rbac/users/{u['id']}/effective-permissions", headers=H)).json()["effective_permissions"] == ["fleet.read"]
