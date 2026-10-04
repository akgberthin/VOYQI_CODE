from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_create_and_list_threshold(client, tenant_id):
    payload = {
        "metric_domain": "spend",
        "metric_key": "total_spend_usd",
        "comparison": "gte",
        "threshold_value": 50000.0,
    }
    resp = await client.post("/api/v1/management/thresholds", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201
    assert resp.json()["is_active"] is True

    resp = await client.get("/api/v1/management/thresholds", headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_breach_triggers_alert_and_resolve_clears_it(client, tenant_id):
    await client.post(
        "/api/v1/management/thresholds",
        json={
            "metric_domain": "spend",
            "metric_key": "total_spend_usd",
            "comparison": "gte",
            "threshold_value": 50000.0,
        },
        headers={"X-Tenant-Id": tenant_id},
    )

    payload = {
        "cost_center_key": "it-shared",
        "metric_domain": "spend",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_spend_usd": 75000.0},
        "source_service": "test",
    }
    resp = await client.post("/api/v1/management/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201

    resp = await client.get("/api/v1/management/alerts", headers={"X-Tenant-Id": tenant_id})
    alerts = resp.json()
    assert len(alerts) == 1
    assert alerts[0]["status"] == "open"

    alert_id = alerts[0]["id"]
    resp = await client.post(f"/api/v1/management/alerts/{alert_id}/resolve", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


@pytest.mark.asyncio
async def test_no_alert_when_threshold_not_breached(client, tenant_id):
    await client.post(
        "/api/v1/management/thresholds",
        json={
            "metric_domain": "spend",
            "metric_key": "total_spend_usd",
            "comparison": "gte",
            "threshold_value": 100000.0,
        },
        headers={"X-Tenant-Id": tenant_id},
    )
    payload = {
        "cost_center_key": "it-shared",
        "metric_domain": "spend",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_spend_usd": 10000.0},
        "source_service": "test",
    }
    await client.post("/api/v1/management/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})

    resp = await client.get("/api/v1/management/alerts", headers={"X-Tenant-Id": tenant_id})
    assert resp.json() == []


@pytest.mark.asyncio
async def test_inactive_threshold_is_not_evaluated(client, tenant_id):
    resp = await client.post(
        "/api/v1/management/thresholds",
        json={
            "metric_domain": "spend",
            "metric_key": "total_spend_usd",
            "comparison": "gte",
            "threshold_value": 50000.0,
        },
        headers={"X-Tenant-Id": tenant_id},
    )
    threshold_id = resp.json()["id"]
    await client.delete(f"/api/v1/management/thresholds/{threshold_id}", headers={"X-Tenant-Id": tenant_id})

    payload = {
        "cost_center_key": "it-shared",
        "metric_domain": "spend",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_spend_usd": 75000.0},
        "source_service": "test",
    }
    await client.post("/api/v1/management/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})

    resp = await client.get("/api/v1/management/alerts", headers={"X-Tenant-Id": tenant_id})
    assert resp.json() == []
