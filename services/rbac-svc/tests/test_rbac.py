from tests.conftest import TENANT_HEADERS


async def _setup_user_and_role(client, permission_keys=None):
    role = (await client.post("/api/v1/rbac/roles", json={"key": "test_role", "name": "Test Role", "permission_keys": permission_keys or ["reports.view"]}, headers=TENANT_HEADERS)).json()
    user = (await client.post("/api/v1/rbac/users", json={"email": "u@voyqi.com", "display_name": "Test User"}, headers=TENANT_HEADERS)).json()
    return user, role


async def test_role_assignment_grants_role_permissions(client):
    user, role = await _setup_user_and_role(client)
    await client.post("/api/v1/rbac/role-assignments", json={"user_id": user["id"], "role_id": role["id"], "assigned_by": "super-admin"}, headers=TENANT_HEADERS)
    perms = (await client.get(f"/api/v1/rbac/users/{user['id']}/effective-permissions", headers=TENANT_HEADERS)).json()
    assert perms["effective_permissions"] == ["reports.view"]


async def test_allow_override_adds_permission_without_a_role(client):
    user, _role = await _setup_user_and_role(client, permission_keys=[])
    await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user["id"], "permission_key": "reports.export", "effect": "allow", "granted_by": "super-admin", "reason": "quarterly board deck"}, headers=TENANT_HEADERS)
    perms = (await client.get(f"/api/v1/rbac/users/{user['id']}/effective-permissions", headers=TENANT_HEADERS)).json()
    assert perms["effective_permissions"] == ["reports.export"]


async def test_deny_override_always_wins_over_role_grant(client):
    """The core deny-wins guarantee: a role grants reports.view, but a
    DENY override on the same permission must win regardless."""
    user, role = await _setup_user_and_role(client, permission_keys=["reports.view", "reports.export"])
    await client.post("/api/v1/rbac/role-assignments", json={"user_id": user["id"], "role_id": role["id"], "assigned_by": "super-admin"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user["id"], "permission_key": "reports.view", "effect": "deny", "granted_by": "super-admin", "reason": "under investigation"}, headers=TENANT_HEADERS)

    perms = (await client.get(f"/api/v1/rbac/users/{user['id']}/effective-permissions", headers=TENANT_HEADERS)).json()
    assert "reports.view" not in perms["effective_permissions"]
    assert "reports.export" in perms["effective_permissions"]  # untouched permission survives
    assert "reports.view" in perms["denied"]


async def test_deny_override_wins_even_with_a_conflicting_allow_override(client):
    """Deny-wins applies across overrides too, not just role vs override."""
    user, _role = await _setup_user_and_role(client, permission_keys=[])
    await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user["id"], "permission_key": "billing.refund", "effect": "allow", "granted_by": "manager-a", "reason": "approved case"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user["id"], "permission_key": "billing.refund", "effect": "deny", "granted_by": "manager-b", "reason": "fraud hold"}, headers=TENANT_HEADERS)

    perms = (await client.get(f"/api/v1/rbac/users/{user['id']}/effective-permissions", headers=TENANT_HEADERS)).json()
    assert "billing.refund" not in perms["effective_permissions"]


async def test_narrow_unjustified_override_auto_revoked_below_threshold(client):
    user, _role = await _setup_user_and_role(client, permission_keys=[])
    override = (await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user["id"], "permission_key": "reports.old_dashboard.view", "effect": "allow", "granted_by": "someone", "reason": ""}, headers=TENANT_HEADERS)).json()
    result = (await client.post(f"/api/v1/rbac/permission-overrides/{override['id']}/auto-revoke-if-unwarranted", json={"threshold": 50}, headers=TENANT_HEADERS)).json()
    assert result["revoked"] is True
    assert result["risk_flagged"] is False


async def test_broad_wildcard_override_escalated_not_auto_revoked(client):
    """A wildcard/super_admin-pattern permission is too high-stakes for
    the agent to unilaterally revoke OR silently leave alone — it must
    be flagged for human review instead, regardless of justification."""
    user, _role = await _setup_user_and_role(client, permission_keys=[])
    override = (await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user["id"], "permission_key": "super_admin.wildcard", "effect": "allow", "granted_by": "someone", "reason": ""}, headers=TENANT_HEADERS)).json()
    result = (await client.post(f"/api/v1/rbac/permission-overrides/{override['id']}/auto-revoke-if-unwarranted", json={"threshold": 50}, headers=TENANT_HEADERS)).json()
    assert result["revoked"] is False
    assert result["risk_flagged"] is True


async def test_deny_effect_never_auto_revoked_as_unwarranted(client):
    """A DENY override narrows access — there's no 'unwarranted' case
    for revoking a restriction, so this must never be auto-revoked."""
    user, _role = await _setup_user_and_role(client, permission_keys=[])
    override = (await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user["id"], "permission_key": "anything.at.all", "effect": "deny", "granted_by": "someone", "reason": ""}, headers=TENANT_HEADERS)).json()
    result = (await client.post(f"/api/v1/rbac/permission-overrides/{override['id']}/auto-revoke-if-unwarranted", json={"threshold": 50}, headers=TENANT_HEADERS)).json()
    assert result["revoked"] is False


async def test_manual_revoke_endpoint_works_independent_of_scoring(client):
    user, _role = await _setup_user_and_role(client, permission_keys=[])
    override = (await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user["id"], "permission_key": "reports.export", "effect": "allow", "granted_by": "manager", "reason": "one-time export"}, headers=TENANT_HEADERS)).json()
    result = (await client.post(f"/api/v1/rbac/permission-overrides/{override['id']}/revoke", json={"revoked_by": "manager", "reason": "task complete"}, headers=TENANT_HEADERS)).json()
    assert result["revoked"] is True
    assert result["revoked_reason"] == "task complete"


async def test_rbac_context_flags_sensitive_permissions(client):
    user, _role = await _setup_user_and_role(client, permission_keys=[])
    await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user["id"], "permission_key": "rbac.grant_role", "effect": "allow", "granted_by": "someone", "reason": "temp project need"}, headers=TENANT_HEADERS)
    context = (await client.get(f"/api/v1/rbac/users/{user['id']}/context", headers=TENANT_HEADERS)).json()
    assert context["active_overrides"][0]["is_sensitive"] is True


async def test_list_all_permission_overrides_is_tenant_wide_not_user_scoped(client):
    user_a, _ = await _setup_user_and_role(client, permission_keys=[])
    user_b = (await client.post("/api/v1/rbac/users", json={"email": "b@voyqi.com", "display_name": "User B"}, headers=TENANT_HEADERS)).json()
    await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user_a["id"], "permission_key": "reports.export", "effect": "allow", "granted_by": "mgr", "reason": "case A"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/permission-overrides", json={"user_id": user_b["id"], "permission_key": "billing.refund", "effect": "allow", "granted_by": "mgr", "reason": "case B"}, headers=TENANT_HEADERS)
    all_overrides = (await client.get("/api/v1/rbac/permission-overrides", headers=TENANT_HEADERS)).json()
    user_ids = {o["user_id"] for o in all_overrides}
    assert user_a["id"] in user_ids and user_b["id"] in user_ids
