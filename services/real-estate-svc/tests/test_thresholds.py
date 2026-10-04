from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_breach_triggers_alert_and_resolve_clears_it(client, tenant_id):
    await client.post(
        "/api/v1/real-estate/thresholds",
        json={"metric_domain": "maintenance", "metric_key": "open_tickets", "comparison": "gte", "threshold_value": 10.0},
        headers={"X-Tenant-Id": tenant_id},
    )

    payload = {
        "property_key": "hub-austin",
        "metric_domain": "maintenance",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"open_tickets": 15.0},
        "source_service": "test",
    }
    resp = await client.post("/api/v1/real-estate/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201

    resp = await client.get("/api/v1/real-estate/alerts", headers={"X-Tenant-Id": tenant_id})
    alerts = resp.json()
    assert len(alerts) == 1
    assert alerts[0]["status"] == "open"

    alert_id = alerts[0]["id"]
    resp = await client.post(f"/api/v1/real-estate/alerts/{alert_id}/resolve", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


@pytest.mark.asyncio
async def test_no_alert_when_threshold_not_breached(client, tenant_id):
    await client.post(
        "/api/v1/real-estate/thresholds",
        json={"metric_domain": "maintenance", "metric_key": "open_tickets", "comparison": "gte", "threshold_value": 10.0},
        headers={"X-Tenant-Id": tenant_id},
    )
    payload = {
        "property_key": "hub-austin",
        "metric_domain": "maintenance",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"open_tickets": 2.0},
        "source_service": "test",
    }
    await client.post("/api/v1/real-estate/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})

    resp = await client.get("/api/v1/real-estate/alerts", headers={"X-Tenant-Id": tenant_id})
    assert resp.json() == []
