from tests.conftest import TENANT_HEADERS


async def test_create_list_suspend_reactivate_tenant(client):
    resp = await client.post("/api/v1/rbac/tenants", json={"tenant_key": "dealer-auto-group", "name": "Auto Group", "subsidiary": "auto_group"})
    assert resp.status_code == 201
    tenant = resp.json()
    assert tenant["status"] == "active"

    listed = (await client.get("/api/v1/rbac/tenants")).json()
    assert any(t["id"] == tenant["id"] for t in listed)

    suspended = (await client.post(f"/api/v1/rbac/tenants/{tenant['id']}/suspend")).json()
    assert suspended["status"] == "suspended"

    reactivated = (await client.post(f"/api/v1/rbac/tenants/{tenant['id']}/reactivate")).json()
    assert reactivated["status"] == "active"


async def test_suspend_nonexistent_tenant_404(client):
    import uuid
    resp = await client.post(f"/api/v1/rbac/tenants/{uuid.uuid4()}/suspend")
    assert resp.status_code == 404
