"""Every access-control state change publishes the event the
Compliance/Audit Agent in ai-orchestration-svc is routed on."""
import pytest

H = {"X-Tenant-Id": "tenant-1"}


@pytest.fixture
def published(monkeypatch):
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append({"topic": topic, "event_type": event_type, "tenant_id": tenant_id, "payload": payload})

    for module in ("rbac", "abac", "modules", "content"):
        monkeypatch.setattr(f"src.api.{module}.publish_event", _spy)
    return events


async def _user_and_role(client):
    user = (await client.post("/api/v1/rbac/users", json={"email": "a@x.com", "display_name": "A"}, headers=H)).json()
    role = (await client.post("/api/v1/rbac/roles", json={"key": "ops", "name": "Ops"}, headers=H)).json()
    return user, role


async def test_role_assignment_publishes_role_assigned(client, published):
    user, role = await _user_and_role(client)
    await client.post("/api/v1/rbac/role-assignments", json={"user_id": user["id"], "role_id": role["id"], "assigned_by": "ai:compliance"}, headers=H)

    [event] = published
    assert event["event_type"] == "role.assigned"
    assert event["topic"] == "rbac.access"
    assert event["tenant_id"] == "tenant-1"
    assert event["payload"]["user_id"] == user["id"]
    assert event["payload"]["ai_initiated"] is True


async def test_permission_grant_and_revoke_publish_events(client, published):
    user, _role = await _user_and_role(client)
    override = (
        await client.post(
            "/api/v1/rbac/permission-overrides",
            json={"user_id": user["id"], "permission_key": "rbac.roles.write", "effect": "allow", "granted_by": "admin-1"},
            headers=H,
        )
    ).json()
    await client.post(f"/api/v1/rbac/permission-overrides/{override['id']}/revoke", json={"revoked_by": "admin-2", "reason": "no longer needed"}, headers=H)

    granted, revoked = published
    assert granted["event_type"] == "permission.granted"
    assert granted["payload"]["is_sensitive"] is True
    assert granted["payload"]["ai_initiated"] is False
    assert revoked["event_type"] == "permission.revoked"
    assert revoked["payload"]["user_id"] == user["id"]


async def test_module_grant_publishes_panel_granted(client, published):
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "fleet", "granted_by": "super-admin"}, headers=H)

    [event] = published
    assert event["event_type"] == "panel.granted"
    assert event["payload"]["module_key"] == "fleet"


async def test_page_publish_publishes_content_published(client, published):
    page = (await client.post("/api/v1/rbac/page-content", json={"page_key": "terms", "title": "Terms"}, headers=H)).json()
    await client.post(f"/api/v1/rbac/page-content/{page['id']}/publish", headers=H)

    [event] = published
    assert event["event_type"] == "content.published"
    assert event["payload"]["page_key"] == "terms"


async def test_abac_apply_publishes_policy_activated(client, published):
    policy = (
        await client.post(
            "/api/v1/rbac/abac-policies",
            json={"name": "GM inherits Sales Manager", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"},
            headers=H,
        )
    ).json()
    await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/simulate", headers=H)
    resp = await client.post(
        f"/api/v1/rbac/abac-policies/{policy['id']}/apply", json={"accountable_role": "ciso", "applied_by": "admin-1"}, headers=H
    )
    assert resp.status_code == 200

    [event] = published
    assert event["event_type"] == "abac_policy.activated"
    assert event["payload"]["accountable_role"] == "ciso"
    # Applied despite the simulation's detected conflicts — exactly the
    # signal the Compliance/Audit Agent scores on.
    assert event["payload"]["had_conflicts_forced"] is bool(resp.json()["conflicts_detected"])


async def test_blocked_abac_apply_publishes_nothing(client, published):
    """The tier-3 gate refusing (no simulation) is not a state change."""
    policy = (
        await client.post(
            "/api/v1/rbac/abac-policies",
            json={"name": "GM inherits Sales Manager", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"},
            headers=H,
        )
    ).json()
    resp = await client.post(
        f"/api/v1/rbac/abac-policies/{policy['id']}/apply", json={"accountable_role": "ciso", "applied_by": "admin-1"}, headers=H
    )
    assert resp.status_code == 409
    assert published == []


async def test_role_revoke_publishes_role_revoked_and_removes_assignment(client, published):
    user, role = await _user_and_role(client)
    assignment = (
        await client.post("/api/v1/rbac/role-assignments", json={"user_id": user["id"], "role_id": role["id"], "assigned_by": "admin-1"}, headers=H)
    ).json()
    resp = await client.post(f"/api/v1/rbac/role-assignments/{assignment['id']}/revoke", json={"revoked_by": "admin-2", "reason": "left team"}, headers=H)
    assert resp.status_code == 200

    revoked = published[-1]
    assert revoked["event_type"] == "role.revoked"
    assert revoked["payload"]["assignment_id"] == assignment["id"]
    context = (await client.get(f"/api/v1/rbac/users/{user['id']}/context", headers=H)).json()
    assert context["roles"] == []

    again = await client.post(f"/api/v1/rbac/role-assignments/{assignment['id']}/revoke", json={"revoked_by": "admin-2"}, headers=H)
    assert again.status_code == 404


async def test_platform_user_can_be_created_with_the_user_svc_id(client, published):
    """The gateway authorizes by the JWT's user_id — the platform user
    must carry that same id for its roles to apply."""
    user_id = "33333333-3333-3333-3333-333333333333"
    created = await client.post("/api/v1/rbac/users", json={"id": user_id, "email": "a@x.com", "display_name": "A"}, headers=H)
    assert created.status_code == 201 and created.json()["id"] == user_id
    role = (await client.post("/api/v1/rbac/roles", json={"key": "fleet_mgr", "name": "Fleet", "permission_keys": ["fleet.read"], "wildcard_permissions": ["rental.*"]}, headers=H)).json()
    await client.post("/api/v1/rbac/role-assignments", json={"user_id": user_id, "role_id": role["id"], "assigned_by": "admin"}, headers=H)

    perms = (await client.get(f"/api/v1/rbac/users/{user_id}/effective-permissions", headers=H)).json()
    assert set(perms["effective_permissions"]) == {"fleet.read", "rental.*"}
    dup = await client.post("/api/v1/rbac/users", json={"id": user_id, "email": "b@x.com", "display_name": "B"}, headers=H)
    assert dup.status_code == 409
