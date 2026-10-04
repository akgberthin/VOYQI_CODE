import pytest


async def _create(client, tenant_id, fico=700, income=60000.0, requested=40000.0):
    resp = await client.post(
        "/api/v1/originations/applications",
        json={"applicant_name": "Jane Doe", "requested_amount_usd": requested, "annual_income_usd": income, "fico_score": fico},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_create_application_defaults_to_draft(client, tenant_id):
    application = await _create(client, tenant_id)
    assert application["status"] == "draft"


@pytest.mark.asyncio
async def test_list_and_get_application(client, tenant_id):
    created = await _create(client, tenant_id)
    resp = await client.get("/api/v1/originations/applications", headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1

    resp = await client.get(f"/api/v1/originations/applications/{created['id']}", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_unknown_application_404(client, tenant_id):
    resp = await client.get(
        "/api/v1/originations/applications/00000000-0000-0000-0000-000000000000", headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_requires_tenant_header(client):
    resp = await client.get("/api/v1/originations/applications")
    assert resp.status_code == 400
