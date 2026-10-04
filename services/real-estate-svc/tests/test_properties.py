import pytest


@pytest.mark.asyncio
async def test_create_and_list_property(client, tenant_id):
    payload = {"key": "hub-austin", "name": "Austin Hub", "property_type": "hub"}
    resp = await client.post("/api/v1/real-estate/properties", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201
    assert resp.json()["status"] == "active"

    resp = await client.get("/api/v1/real-estate/properties", headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_filter_by_type(client, tenant_id):
    await client.post(
        "/api/v1/real-estate/properties",
        json={"key": "hub-austin", "name": "Austin Hub", "property_type": "hub"},
        headers={"X-Tenant-Id": tenant_id},
    )
    await client.post(
        "/api/v1/real-estate/properties",
        json={"key": "office-hq", "name": "HQ Office", "property_type": "office"},
        headers={"X-Tenant-Id": tenant_id},
    )

    resp = await client.get(
        "/api/v1/real-estate/properties", params={"property_type": "office"}, headers={"X-Tenant-Id": tenant_id}
    )
    body = resp.json()
    assert len(body) == 1
    assert body[0]["key"] == "office-hq"


@pytest.mark.asyncio
async def test_deactivate_property(client, tenant_id):
    resp = await client.post(
        "/api/v1/real-estate/properties",
        json={"key": "hub-austin", "name": "Austin Hub", "property_type": "hub"},
        headers={"X-Tenant-Id": tenant_id},
    )
    prop_id = resp.json()["id"]

    resp = await client.delete(f"/api/v1/real-estate/properties/{prop_id}", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_deactivate_unknown_property_returns_404(client, tenant_id):
    resp = await client.delete(
        "/api/v1/real-estate/properties/00000000-0000-0000-0000-000000000000",
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_requires_tenant_header(client):
    resp = await client.get("/api/v1/real-estate/properties")
    assert resp.status_code == 400
