from tests.conftest import TENANT_HEADERS


async def _setup_roles(client):
    await client.post("/api/v1/rbac/roles", json={"key": "dealer_gm", "name": "Dealer GM", "permission_keys": ["reports.view"], "is_system_role": True}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/roles", json={"key": "sales_manager", "name": "Sales Manager", "permission_keys": ["deals.approve", "inventory.view"]}, headers=TENANT_HEADERS)


async def test_simulate_is_fully_autonomous_no_gate(client):
    await _setup_roles(client)
    policy = (await client.post("/api/v1/rbac/abac-policies", json={"name": "GM inherits Sales Manager", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"}, headers=TENANT_HEADERS)).json()
    resp = await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/simulate", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "simulated"


async def test_simulate_can_be_rerun_freely(client):
    """No approval needed to preview — calling it repeatedly is fine."""
    await _setup_roles(client)
    policy = (await client.post("/api/v1/rbac/abac-policies", json={"name": "GM inherits Sales Manager", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"}, headers=TENANT_HEADERS)).json()
    for _ in range(3):
        resp = await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/simulate", headers=TENANT_HEADERS)
        assert resp.status_code == 200


async def test_apply_rejects_empty_accountable_role(client):
    await _setup_roles(client)
    policy = (await client.post("/api/v1/rbac/abac-policies", json={"name": "GM inherits Sales Manager", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"}, headers=TENANT_HEADERS)).json()
    await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/simulate", headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/apply", json={"accountable_role": "", "applied_by": "garth"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


async def test_apply_rejects_whitespace_only_accountable_role(client):
    await _setup_roles(client)
    policy = (await client.post("/api/v1/rbac/abac-policies", json={"name": "GM inherits Sales Manager", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"}, headers=TENANT_HEADERS)).json()
    await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/simulate", headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/apply", json={"accountable_role": "   ", "applied_by": "garth"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


async def test_apply_rejects_unsimulated_policy(client):
    """A policy must be previewed at least once before it can be
    applied — no skipping straight from draft to applied."""
    await _setup_roles(client)
    policy = (await client.post("/api/v1/rbac/abac-policies", json={"name": "GM inherits Sales Manager", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"}, headers=TENANT_HEADERS)).json()
    resp = await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/apply", json={"accountable_role": "VP Sales", "applied_by": "garth"}, headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_apply_succeeds_with_real_accountable_role_after_simulation(client):
    await _setup_roles(client)
    policy = (await client.post("/api/v1/rbac/abac-policies", json={"name": "GM inherits Sales Manager", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"}, headers=TENANT_HEADERS)).json()
    await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/simulate", headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/apply", json={"accountable_role": "VP Sales Operations", "applied_by": "garth"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "applied"
    assert body["accountable_role"] == "VP Sales Operations"
    assert body["applied_by"] == "garth"


async def test_apply_records_an_audit_log_entry(client):
    await _setup_roles(client)
    policy = (await client.post("/api/v1/rbac/abac-policies", json={"name": "GM inherits Sales Manager", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"}, headers=TENANT_HEADERS)).json()
    await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/simulate", headers=TENANT_HEADERS)
    await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/apply", json={"accountable_role": "VP Sales Operations", "applied_by": "garth"}, headers=TENANT_HEADERS)
    log = (await client.get("/api/v1/rbac/audit-log", headers=TENANT_HEADERS)).json()
    assert any(e["action"] == "abac_policy.applied" and e["actor_id"] == "garth" for e in log)


async def test_reject_policy(client):
    await _setup_roles(client)
    policy = (await client.post("/api/v1/rbac/abac-policies", json={"name": "GM inherits Sales Manager", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"}, headers=TENANT_HEADERS)).json()
    resp = await client.post(f"/api/v1/rbac/abac-policies/{policy['id']}/reject", headers=TENANT_HEADERS)
    assert resp.json()["status"] == "rejected"


async def test_list_policies_returns_all_for_tenant(client):
    await _setup_roles(client)
    await client.post("/api/v1/rbac/abac-policies", json={"name": "Policy A", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/abac-policies", json={"name": "Policy B", "source_role_key": "dealer_gm", "target_bundle_key": "sales_manager"}, headers=TENANT_HEADERS)
    listed = (await client.get("/api/v1/rbac/abac-policies", headers=TENANT_HEADERS)).json()
    names = {p["name"] for p in listed}
    assert {"Policy A", "Policy B"}.issubset(names)
