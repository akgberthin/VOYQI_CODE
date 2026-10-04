from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_create_and_list_threshold(client, tenant_id):
    payload = {
        "metric_domain": "revenue",
        "metric_key": "total_revenue_usd",
        "comparison": "lte",
        "threshold_value": 50000.0,
    }
    resp = await client.post("/api/v1/executive/thresholds", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201
    assert resp.json()["is_active"] is True

    resp = await client.get("/api/v1/executive/thresholds", headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_deactivate_threshold(client, tenant_id):
    payload = {
        "metric_domain": "revenue",
        "metric_key": "total_revenue_usd",
        "comparison": "lte",
        "threshold_value": 50000.0,
    }
    resp = await client.post("/api/v1/executive/thresholds", json=payload, headers={"X-Tenant-Id": tenant_id})
    threshold_id = resp.json()["id"]

    resp = await client.delete(f"/api/v1/executive/thresholds/{threshold_id}", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_unknown_threshold_returns_404(client, tenant_id):
    resp = await client.delete(
        "/api/v1/executive/thresholds/00000000-0000-0000-0000-000000000000",
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_breach_triggers_alert_and_resolve_clears_it(client, tenant_id):
    await client.post(
        "/api/v1/executive/thresholds",
        json={
            "metric_domain": "revenue",
            "metric_key": "total_revenue_usd",
            "comparison": "lte",
            "threshold_value": 50000.0,
        },
        headers={"X-Tenant-Id": tenant_id},
    )

    payload = {
        "subsidiary_key": "voyqi-rental",
        "metric_domain": "revenue",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_revenue_usd": 20000.0},
        "source_service": "test",
    }
    resp = await client.post("/api/v1/executive/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201

    resp = await client.get("/api/v1/executive/alerts", headers={"X-Tenant-Id": tenant_id})
    alerts = resp.json()
    assert len(alerts) == 1
    assert alerts[0]["status"] == "open"
    assert alerts[0]["observed_value"] == 20000.0

    alert_id = alerts[0]["id"]
    resp = await client.post(f"/api/v1/executive/alerts/{alert_id}/resolve", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"

    resp = await client.get("/api/v1/executive/alerts", params={"status": "open"}, headers={"X-Tenant-Id": tenant_id})
    assert resp.json() == []


@pytest.mark.asyncio
async def test_no_alert_when_threshold_not_breached(client, tenant_id):
    await client.post(
        "/api/v1/executive/thresholds",
        json={
            "metric_domain": "revenue",
            "metric_key": "total_revenue_usd",
            "comparison": "gte",
            "threshold_value": 100000.0,
        },
        headers={"X-Tenant-Id": tenant_id},
    )
    payload = {
        "subsidiary_key": "voyqi-rental",
        "metric_domain": "revenue",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_revenue_usd": 50000.0},
        "source_service": "test",
    }
    await client.post("/api/v1/executive/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})

    resp = await client.get("/api/v1/executive/alerts", headers={"X-Tenant-Id": tenant_id})
    assert resp.json() == []


@pytest.mark.asyncio
async def test_inactive_threshold_is_not_evaluated(client, tenant_id):
    resp = await client.post(
        "/api/v1/executive/thresholds",
        json={
            "metric_domain": "revenue",
            "metric_key": "total_revenue_usd",
            "comparison": "lte",
            "threshold_value": 50000.0,
        },
        headers={"X-Tenant-Id": tenant_id},
    )
    threshold_id = resp.json()["id"]
    await client.delete(f"/api/v1/executive/thresholds/{threshold_id}", headers={"X-Tenant-Id": tenant_id})

    payload = {
        "subsidiary_key": "voyqi-rental",
        "metric_domain": "revenue",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_revenue_usd": 20000.0},
        "source_service": "test",
    }
    await client.post("/api/v1/executive/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})

    resp = await client.get("/api/v1/executive/alerts", headers={"X-Tenant-Id": tenant_id})
    assert resp.json() == []
