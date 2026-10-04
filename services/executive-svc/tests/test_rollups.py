from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_ingest_and_list_snapshot(client, tenant_id):
    payload = {
        "subsidiary_key": "voyqi-rental",
        "metric_domain": "revenue",
        "period_start": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 1, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_revenue_usd": 125000.0, "booking_count": 340},
        "source_service": "rental-svc",
    }
    resp = await client.post("/api/v1/executive/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["subsidiary_key"] == "voyqi-rental"
    assert body["metrics"]["total_revenue_usd"] == 125000.0

    resp = await client.get("/api/v1/executive/rollups", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_requires_tenant_header(client):
    resp = await client.get("/api/v1/executive/rollups")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_filters_by_subsidiary(client, tenant_id):
    base = {
        "period_start": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 1, 31, tzinfo=timezone.utc).isoformat(),
        "source_service": "rental-svc",
        "metric_domain": "revenue",
    }
    await client.post(
        "/api/v1/executive/rollups",
        json={**base, "subsidiary_key": "voyqi-rental", "metrics": {"total_revenue_usd": 100.0}},
        headers={"X-Tenant-Id": tenant_id},
    )
    await client.post(
        "/api/v1/executive/rollups",
        json={**base, "subsidiary_key": "voyqi-dealer", "metrics": {"total_revenue_usd": 200.0}},
        headers={"X-Tenant-Id": tenant_id},
    )

    resp = await client.get(
        "/api/v1/executive/rollups",
        params={"subsidiary_key": "voyqi-dealer"},
        headers={"X-Tenant-Id": tenant_id},
    )
    body = resp.json()
    assert len(body) == 1
    assert body[0]["subsidiary_key"] == "voyqi-dealer"


@pytest.mark.asyncio
async def test_tenants_are_isolated(client, tenant_id):
    payload = {
        "subsidiary_key": "voyqi-rental",
        "metric_domain": "revenue",
        "period_start": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 1, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_revenue_usd": 100.0},
        "source_service": "rental-svc",
    }
    await client.post("/api/v1/executive/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})

    resp = await client.get("/api/v1/executive/rollups", headers={"X-Tenant-Id": "some-other-tenant"})
    assert resp.json() == []
