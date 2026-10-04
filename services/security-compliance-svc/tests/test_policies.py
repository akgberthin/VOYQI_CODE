import pytest


@pytest.mark.asyncio
async def test_create_and_list_policy(client, tenant_id):
    resp = await client.post(
        "/api/v1/security-compliance/policies",
        json={"key": "abac-sensitive-data", "name": "Sensitive Data Access", "description": "Grants read access to PII fields"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "active"

    resp = await client.get("/api/v1/security-compliance/policies", headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_requires_tenant_header(client):
    resp = await client.get("/api/v1/security-compliance/policies")
    assert resp.status_code == 400
