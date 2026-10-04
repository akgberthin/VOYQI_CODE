from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_covenant_breach_triggers_alert_and_resolve_clears_it(client, tenant_id):
    await client.post(
        "/api/v1/capital-finance/thresholds",
        json={
            "metric_domain": "covenant_headroom",
            "metric_key": "headroom_pct",
            "comparison": "lte",
            "threshold_value": 10.0,
        },
        headers={"X-Tenant-Id": tenant_id},
    )

    payload = {
        "funding_source_key": "term-loan-a",
        "metric_domain": "covenant_headroom",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"headroom_pct": 4.5},
        "source_service": "test",
    }
    resp = await client.post("/api/v1/capital-finance/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201

    resp = await client.get("/api/v1/capital-finance/alerts", headers={"X-Tenant-Id": tenant_id})
    alerts = resp.json()
    assert len(alerts) == 1
    assert alerts[0]["status"] == "open"
    assert alerts[0]["metric_key"] == "headroom_pct"

    alert_id = alerts[0]["id"]
    resp = await client.post(
        f"/api/v1/capital-finance/alerts/{alert_id}/resolve", headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


@pytest.mark.asyncio
async def test_no_alert_when_headroom_healthy(client, tenant_id):
    await client.post(
        "/api/v1/capital-finance/thresholds",
        json={
            "metric_domain": "covenant_headroom",
            "metric_key": "headroom_pct",
            "comparison": "lte",
            "threshold_value": 10.0,
        },
        headers={"X-Tenant-Id": tenant_id},
    )
    payload = {
        "funding_source_key": "term-loan-a",
        "metric_domain": "covenant_headroom",
        "period_start": datetime(2026, 3, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 3, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"headroom_pct": 25.0},
        "source_service": "test",
    }
    await client.post("/api/v1/capital-finance/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})

    resp = await client.get("/api/v1/capital-finance/alerts", headers={"X-Tenant-Id": tenant_id})
    assert resp.json() == []
