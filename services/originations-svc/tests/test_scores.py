import pytest


async def _create(client, tenant_id, fico, income, requested):
    resp = await client.post(
        "/api/v1/originations/applications",
        json={"applicant_name": "Jane Doe", "requested_amount_usd": requested, "annual_income_usd": income, "fico_score": fico},
        headers={"X-Tenant-Id": tenant_id},
    )
    return resp.json()


@pytest.mark.asyncio
async def test_high_fico_high_ratio_scores_hot(client, tenant_id):
    application = await _create(client, tenant_id, fico=850, income=100000.0, requested=50000.0)
    resp = await client.post(
        f"/api/v1/originations/applications/{application['id']}/scores", headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["score"] == pytest.approx(100.0)
    assert body["band"] == "hot"

    app_resp = await client.get(f"/api/v1/originations/applications/{application['id']}", headers={"X-Tenant-Id": tenant_id})
    assert app_resp.json()["status"] == "scored"


@pytest.mark.asyncio
async def test_moderate_fico_moderate_ratio_scores_warm(client, tenant_id):
    application = await _create(client, tenant_id, fico=740, income=60000.0, requested=60000.0)
    resp = await client.post(
        f"/api/v1/originations/applications/{application['id']}/scores", headers={"X-Tenant-Id": tenant_id}
    )
    body = resp.json()
    assert body["score"] == pytest.approx(65.0)
    assert body["band"] == "warm"


@pytest.mark.asyncio
async def test_low_fico_low_ratio_scores_cold(client, tenant_id):
    application = await _create(client, tenant_id, fico=300, income=10000.0, requested=100000.0)
    resp = await client.post(
        f"/api/v1/originations/applications/{application['id']}/scores", headers={"X-Tenant-Id": tenant_id}
    )
    body = resp.json()
    assert body["score"] == pytest.approx(2.5)
    assert body["band"] == "cold"


@pytest.mark.asyncio
async def test_score_unknown_application_404s(client, tenant_id):
    resp = await client.post(
        "/api/v1/originations/applications/00000000-0000-0000-0000-000000000000/scores",
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_scores_returns_history(client, tenant_id):
    application = await _create(client, tenant_id, fico=700, income=60000.0, requested=40000.0)
    await client.post(f"/api/v1/originations/applications/{application['id']}/scores", headers={"X-Tenant-Id": tenant_id})
    await client.post(f"/api/v1/originations/applications/{application['id']}/scores", headers={"X-Tenant-Id": tenant_id})

    resp = await client.get(f"/api/v1/originations/applications/{application['id']}/scores", headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 2
