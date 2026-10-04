from datetime import datetime, timezone

import pytest


async def _ingest(client, tenant_id, cost_center_key, spend, period_start=datetime(2026, 2, 1, tzinfo=timezone.utc)):
    payload = {
        "cost_center_key": cost_center_key,
        "metric_domain": "spend",
        "period_start": period_start.isoformat(),
        "period_end": datetime(2026, 2, 28, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_spend_usd": spend},
        "source_service": "test",
    }
    resp = await client.post("/api/v1/management/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_cross_cost_center_summary_sums_totals(client, tenant_id):
    await _ingest(client, tenant_id, "it-shared", 40000.0)
    await _ingest(client, tenant_id, "facilities-hq", 15000.0)

    resp = await client.get(
        "/api/v1/management/dashboard/summary",
        params={"metric_domain": "spend"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cost_center_count"] == 2
    assert body["totals"]["total_spend_usd"] == 55000.0


@pytest.mark.asyncio
async def test_summary_uses_latest_snapshot_per_cost_center(client, tenant_id):
    await _ingest(client, tenant_id, "it-shared", 40000.0, period_start=datetime(2026, 1, 1, tzinfo=timezone.utc))
    await _ingest(client, tenant_id, "it-shared", 55000.0, period_start=datetime(2026, 2, 1, tzinfo=timezone.utc))

    resp = await client.get(
        "/api/v1/management/dashboard/summary",
        params={"metric_domain": "spend"},
        headers={"X-Tenant-Id": tenant_id},
    )
    body = resp.json()
    assert body["cost_center_count"] == 1
    assert body["totals"]["total_spend_usd"] == 55000.0


@pytest.mark.asyncio
async def test_trend_returns_ordered_snapshots(client, tenant_id):
    await _ingest(client, tenant_id, "it-shared", 40000.0)

    resp = await client.get(
        "/api/v1/management/dashboard/trend",
        params={"cost_center_key": "it-shared", "metric_domain": "spend"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1
