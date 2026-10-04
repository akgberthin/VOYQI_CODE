import pytest


@pytest.mark.asyncio
async def test_create_and_list_funding_source(client, tenant_id):
    payload = {"key": "term-loan-a", "name": "Term Loan A", "source_type": "debt"}
    resp = await client.post(
        "/api/v1/capital-finance/funding-sources", json=payload, headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "active"

    resp = await client.get("/api/v1/capital-finance/funding-sources", headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_filter_by_source_type(client, tenant_id):
    await client.post(
        "/api/v1/capital-finance/funding-sources",
        json={"key": "term-loan-a", "name": "Term Loan A", "source_type": "debt"},
        headers={"X-Tenant-Id": tenant_id},
    )
    await client.post(
        "/api/v1/capital-finance/funding-sources",
        json={"key": "series-b", "name": "Series B", "source_type": "equity"},
        headers={"X-Tenant-Id": tenant_id},
    )

    resp = await client.get(
        "/api/v1/capital-finance/funding-sources",
        params={"source_type": "equity"},
        headers={"X-Tenant-Id": tenant_id},
    )
    body = resp.json()
    assert len(body) == 1
    assert body[0]["key"] == "series-b"


@pytest.mark.asyncio
async def test_deactivate_funding_source(client, tenant_id):
    resp = await client.post(
        "/api/v1/capital-finance/funding-sources",
        json={"key": "term-loan-a", "name": "Term Loan A", "source_type": "debt"},
        headers={"X-Tenant-Id": tenant_id},
    )
    source_id = resp.json()["id"]

    resp = await client.delete(
        f"/api/v1/capital-finance/funding-sources/{source_id}", headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_requires_tenant_header(client):
    resp = await client.get("/api/v1/capital-finance/funding-sources")
    assert resp.status_code == 400
