from datetime import datetime, timezone

import pytest


async def _ingest(client, tenant_id, property_key, occupancy, period_start=datetime(2026, 2, 1, tzinfo=timezone.utc)):
    payload = {
        "property_key": property_key,
        "metric_domain": "occupancy",
        "period_start": period_start.isoformat(),
        "period_end": datetime(2026, 2, 28, tzinfo=timezone.utc).isoformat(),
        "metrics": {"occupied_sqft": occupancy},
        "source_service": "test",
    }
    resp = await client.post("/api/v1/real-estate/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_cross_property_summary_sums_totals(client, tenant_id):
    await _ingest(client, tenant_id, "hub-austin", 5000.0)
    await _ingest(client, tenant_id, "office-hq", 12000.0)

    resp = await client.get(
        "/api/v1/real-estate/dashboard/summary",
        params={"metric_domain": "occupancy"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["property_count"] == 2
    assert body["totals"]["occupied_sqft"] == 17000.0


@pytest.mark.asyncio
async def test_trend_returns_ordered_snapshots(client, tenant_id):
    await _ingest(client, tenant_id, "hub-austin", 5000.0)

    resp = await client.get(
        "/api/v1/real-estate/dashboard/trend",
        params={"property_key": "hub-austin", "metric_domain": "occupancy"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1
