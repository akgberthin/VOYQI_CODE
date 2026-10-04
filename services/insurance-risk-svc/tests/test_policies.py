import pytest


async def _create(client, tenant_id, coverage=500000.0, policy_type="auto"):
    resp = await client.post(
        "/api/v1/insurance-risk/policies",
        json={"policy_number": "POL-1", "policy_type": policy_type, "insured_name": "Jane Doe", "coverage_amount_usd": coverage},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_create_and_get_policy(client, tenant_id):
    policy = await _create(client, tenant_id)
    assert policy["status"] == "active"

    resp = await client.get(f"/api/v1/insurance-risk/policies/{policy['id']}", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["policy_number"] == "POL-1"


@pytest.mark.asyncio
async def test_list_and_filter_by_status(client, tenant_id):
    await _create(client, tenant_id)
    resp = await client.get("/api/v1/insurance-risk/policies", params={"status": "cancelled"}, headers={"X-Tenant-Id": tenant_id})
    assert resp.json() == []


@pytest.mark.asyncio
async def test_set_policy_status(client, tenant_id):
    policy = await _create(client, tenant_id)
    resp = await client.post(
        f"/api/v1/insurance-risk/policies/{policy['id']}/status",
        json={"status": "lapsed"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "lapsed"


@pytest.mark.asyncio
async def test_get_unknown_policy_404(client, tenant_id):
    resp = await client.get(
        "/api/v1/insurance-risk/policies/00000000-0000-0000-0000-000000000000", headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_requires_tenant_header(client):
    resp = await client.get("/api/v1/insurance-risk/policies")
    assert resp.status_code == 400
