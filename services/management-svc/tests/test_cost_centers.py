import pytest


@pytest.mark.asyncio
async def test_create_and_list_cost_center(client, tenant_id):
    payload = {"key": "it-shared", "name": "IT Shared Services", "category": "it"}
    resp = await client.post("/api/v1/management/cost-centers", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "active"

    resp = await client.get("/api/v1/management/cost-centers", headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_filter_by_category(client, tenant_id):
    await client.post(
        "/api/v1/management/cost-centers",
        json={"key": "it-shared", "name": "IT Shared Services", "category": "it"},
        headers={"X-Tenant-Id": tenant_id},
    )
    await client.post(
        "/api/v1/management/cost-centers",
        json={"key": "facilities-hq", "name": "HQ Facilities", "category": "facilities"},
        headers={"X-Tenant-Id": tenant_id},
    )

    resp = await client.get(
        "/api/v1/management/cost-centers", params={"category": "facilities"}, headers={"X-Tenant-Id": tenant_id}
    )
    body = resp.json()
    assert len(body) == 1
    assert body[0]["key"] == "facilities-hq"


@pytest.mark.asyncio
async def test_deactivate_cost_center(client, tenant_id):
    resp = await client.post(
        "/api/v1/management/cost-centers",
        json={"key": "it-shared", "name": "IT Shared Services", "category": "it"},
        headers={"X-Tenant-Id": tenant_id},
    )
    cc_id = resp.json()["id"]

    resp = await client.delete(f"/api/v1/management/cost-centers/{cc_id}", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_deactivate_unknown_cost_center_returns_404(client, tenant_id):
    resp = await client.delete(
        "/api/v1/management/cost-centers/00000000-0000-0000-0000-000000000000",
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_requires_tenant_header(client):
    resp = await client.get("/api/v1/management/cost-centers")
    assert resp.status_code == 400
