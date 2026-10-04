from tests.conftest import TENANT_HEADERS


async def test_grant_and_revoke_module(client):
    resp = await client.post("/api/v1/rbac/module-grants", json={"module_key": "dealer-center", "label": "Dealer Center"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["enabled"] is True

    listed = (await client.get("/api/v1/rbac/module-grants", headers=TENANT_HEADERS)).json()
    assert any(m["module_key"] == "dealer-center" for m in listed)

    revoked = (await client.post("/api/v1/rbac/module-grants/dealer-center/revoke", headers=TENANT_HEADERS)).json()
    assert revoked["enabled"] is False


async def test_regrant_reenables_existing_grant_rather_than_duplicating(client):
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "fleet-management"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/module-grants/fleet-management/revoke", headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "fleet-management"}, headers=TENANT_HEADERS)
    listed = (await client.get("/api/v1/rbac/module-grants", headers=TENANT_HEADERS)).json()
    matches = [m for m in listed if m["module_key"] == "fleet-management"]
    assert len(matches) == 1
    assert matches[0]["enabled"] is True


async def test_revoke_nonexistent_module_404(client):
    resp = await client.post("/api/v1/rbac/module-grants/does-not-exist/revoke", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_sub_module_requires_enabled_parent(client):
    """A sub-section can't be granted before its parent module exists
    and is enabled — this is the hierarchy rule that makes 'buttons
    for each sub-section' meaningful rather than a flat, unrelated list."""
    resp = await client.post("/api/v1/rbac/module-grants", json={"module_key": "flt-003-staging", "parent_module_key": "fleet-management", "label": "FLT-003 Staging"}, headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_sub_module_grantable_once_parent_enabled(client):
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "fleet-management", "label": "Fleet Management"}, headers=TENANT_HEADERS)
    resp = await client.post("/api/v1/rbac/module-grants", json={"module_key": "flt-003-staging", "parent_module_key": "fleet-management", "label": "FLT-003 Staging"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["parent_module_key"] == "fleet-management"


async def test_revoking_parent_cascades_to_sub_sections(client):
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "dealer-center", "label": "Dealer Center"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "dms-005-appraisal", "parent_module_key": "dealer-center", "label": "DMS-005 Trade Appraisal"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "dms-006-service", "parent_module_key": "dealer-center", "label": "DMS-006 Service"}, headers=TENANT_HEADERS)

    await client.post("/api/v1/rbac/module-grants/dealer-center/revoke", headers=TENANT_HEADERS)

    listed = (await client.get("/api/v1/rbac/module-grants", headers=TENANT_HEADERS)).json()
    children = [m for m in listed if m["parent_module_key"] == "dealer-center"]
    assert len(children) == 2
    assert all(c["enabled"] is False for c in children)
