"""User edit / suspend / unlock / last seen / assignments list."""
import uuid

import pytest

from src.services import account_status

H = {"X-Tenant-Id": "voyqi-demo"}


@pytest.fixture
def auth(monkeypatch):
    """Stands in for auth-svc's /internal/accounts."""
    state = {"accounts": {}, "down": False, "unlocked": []}

    async def fetch(tenant_id, user_ids=None):
        if state["down"]:
            raise account_status.AccountServiceError("auth-svc unreachable: ConnectError")
        return {k: v for k, v in state["accounts"].items() if not user_ids or k in user_ids}

    async def unlock(tenant_id, user_id):
        if state["down"]:
            raise account_status.AccountServiceError("auth-svc unreachable: ConnectError")
        if user_id not in state["accounts"]:
            raise LookupError("This user has no sign-in account to unlock.")
        state["accounts"][user_id].update(locked=False, locked_until=None, failed_login_attempts=0)
        state["unlocked"].append(user_id)
        return state["accounts"][user_id]

    monkeypatch.setattr(account_status, "fetch", fetch)
    monkeypatch.setattr(account_status, "unlock", unlock)
    return state


async def _user(client, email="ops@voyqi.com", name="Ops"):
    r = await client.post("/api/v1/rbac/users", json={"email": email, "display_name": name}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _role(client, key="fleet_manager", perms=("fleet.read",)):
    return (await client.post("/api/v1/rbac/roles", json={"key": key, "name": key, "permission_keys": list(perms)}, headers=H)).json()


async def test_list_merges_sign_in_state_and_roles(client, auth):
    u = await _user(client)
    other = await _user(client, "no-login@voyqi.com", "No login")
    role = await _role(client)
    await client.post("/api/v1/rbac/role-assignments", json={"user_id": u["id"], "role_id": role["id"], "assigned_by": "admin"}, headers=H)
    auth["accounts"][u["id"]] = {"user_id": u["id"], "locked": True, "locked_until": "2026-10-30T10:00:00+00:00", "failed_login_attempts": 5,
                                 "last_login_at": "2026-10-29T09:00:00+00:00", "mfa_enabled": True}
    rows = {r["id"]: r for r in (await client.get("/api/v1/rbac/users", headers=H)).json()}
    assert rows[u["id"]]["locked"] is True and rows[u["id"]]["failed_login_attempts"] == 5 and rows[u["id"]]["login_mfa_enabled"] is True
    assert rows[u["id"]]["role_keys"] == ["fleet_manager"] and rows[u["id"]]["created_at"]
    assert rows[other["id"]]["has_login"] is False and rows[other["id"]]["locked"] is None

    auth["down"] = True
    rows = (await client.get("/api/v1/rbac/users", headers=H)).json()
    assert all("unreachable" in r["account_status_error"] for r in rows) and len(rows) == 2


async def test_unlock_goes_through_auth_and_is_audited(client, auth):
    u = await _user(client)
    auth["accounts"][u["id"]] = {"user_id": u["id"], "locked": True, "locked_until": "x", "failed_login_attempts": 5, "last_login_at": None, "mfa_enabled": False}
    r = await client.post(f"/api/v1/rbac/users/{u['id']}/unlock", json={"unlocked_by": "admin@voyqi.com"}, headers=H)
    assert r.status_code == 200 and r.json()["locked"] is False and auth["unlocked"] == [u["id"]]
    audit = (await client.get("/api/v1/rbac/audit-log", headers=H)).json()
    assert any(a["action"] == "platform_user.unlocked" and a["actor_id"] == "admin@voyqi.com" for a in audit)

    nologin = await _user(client, "x@voyqi.com", "X")
    assert (await client.post(f"/api/v1/rbac/users/{nologin['id']}/unlock", json={"unlocked_by": "a"}, headers=H)).status_code == 404
    assert (await client.post(f"/api/v1/rbac/users/{uuid.uuid4()}/unlock", json={"unlocked_by": "a"}, headers=H)).status_code == 404
    auth["down"] = True
    assert (await client.post(f"/api/v1/rbac/users/{u['id']}/unlock", json={"unlocked_by": "a"}, headers=H)).status_code == 503


async def test_edit_and_suspend_removes_permissions(client, auth):
    u = await _user(client)
    role = await _role(client, perms=("fleet.read", "fleet.write"))
    await client.post("/api/v1/rbac/role-assignments", json={"user_id": u["id"], "role_id": role["id"], "assigned_by": "admin"}, headers=H)
    eff = f"/api/v1/rbac/users/{u['id']}/effective-permissions"
    assert (await client.get(eff, headers=H)).json()["effective_permissions"] == ["fleet.read", "fleet.write"]

    r = await client.patch(f"/api/v1/rbac/users/{u['id']}", json={"display_name": "Ops Lead", "mfa_enabled": True, "updated_by": "admin"}, headers=H)
    assert r.status_code == 200 and r.json()["display_name"] == "Ops Lead" and r.json()["mfa_enabled"] is True

    r = await client.patch(f"/api/v1/rbac/users/{u['id']}", json={"status": "suspended", "updated_by": "admin", "reason": "left the company"}, headers=H)
    assert r.json()["status"] == "suspended"
    body = (await client.get(eff, headers=H)).json()
    assert body["effective_permissions"] == [] and body["suspended"] is True
    # roles stay on record, so reactivating restores access
    assert len((await client.get("/api/v1/rbac/role-assignments", headers=H)).json()) == 1
    await client.patch(f"/api/v1/rbac/users/{u['id']}", json={"status": "active", "updated_by": "admin"}, headers=H)
    assert (await client.get(eff, headers=H)).json()["effective_permissions"] == ["fleet.read", "fleet.write"]

    actions = [a["action"] for a in (await client.get("/api/v1/rbac/audit-log", headers=H)).json()]
    assert {"platform_user.updated", "platform_user.suspended", "platform_user.reactivated"} <= set(actions)
    detail = next(a["detail"] for a in (await client.get("/api/v1/rbac/audit-log", headers=H)).json() if a["action"] == "platform_user.suspended")
    assert "active → suspended" in detail and "left the company" in detail


async def test_edit_validation(client, auth):
    a = await _user(client)
    b = await _user(client, "b@voyqi.com", "B")
    url = f"/api/v1/rbac/users/{a['id']}"
    assert (await client.patch(url, json={"updated_by": "admin"}, headers=H)).status_code == 422
    assert (await client.patch(url, json={"status": "deleted", "updated_by": "admin"}, headers=H)).status_code == 422
    assert (await client.patch(url, json={"email": b["email"], "updated_by": "admin"}, headers=H)).status_code == 409
    assert (await client.patch(f"/api/v1/rbac/users/{uuid.uuid4()}", json={"display_name": "x", "updated_by": "admin"}, headers=H)).status_code == 404
    # no-op edit writes no audit entry
    before = len((await client.get("/api/v1/rbac/audit-log", headers=H)).json())
    await client.patch(url, json={"display_name": a["display_name"], "updated_by": "admin"}, headers=H)
    assert len((await client.get("/api/v1/rbac/audit-log", headers=H)).json()) == before


async def test_last_seen_is_stamped_only_when_asked(client, auth):
    u = await _user(client)
    eff = f"/api/v1/rbac/users/{u['id']}/effective-permissions"
    await client.get(eff, headers=H)
    row = lambda users: next(x for x in users if x["id"] == u["id"])  # noqa: E731
    assert row((await client.get("/api/v1/rbac/users", headers=H)).json())["last_seen_at"] is None
    await client.get(eff, headers={**H, "X-Record-Seen": "1"})
    first = row((await client.get("/api/v1/rbac/users", headers=H)).json())["last_seen_at"]
    assert first
    await client.get(eff, headers={**H, "X-Record-Seen": "1"})  # within the minute: unchanged
    assert row((await client.get("/api/v1/rbac/users", headers=H)).json())["last_seen_at"] == first


async def test_assignments_list_is_tenant_scoped(client, auth):
    u = await _user(client)
    role = await _role(client)
    await client.post("/api/v1/rbac/role-assignments", json={"user_id": u["id"], "role_id": role["id"], "assigned_by": "admin"}, headers=H)
    rows = (await client.get("/api/v1/rbac/role-assignments", headers=H)).json()
    assert len(rows) == 1 and rows[0]["assigned_at"] and rows[0]["role_id"] == role["id"]
    assert (await client.get("/api/v1/rbac/role-assignments", headers={"X-Tenant-Id": "other"})).json() == []
