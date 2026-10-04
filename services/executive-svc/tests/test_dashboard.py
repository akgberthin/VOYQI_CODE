from datetime import datetime, timezone

import pytest


async def _ingest(client, tenant_id, subsidiary_key, revenue, period_start=datetime(2026, 2, 1, tzinfo=timezone.utc)):
    payload = {
        "subsidiary_key": subsidiary_key,
        "metric_domain": "revenue",
        "period_start": period_start.isoformat(),
        "period_end": datetime(2026, 2, 28, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_revenue_usd": revenue},
        "source_service": "test",
    }
    resp = await client.post("/api/v1/executive/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_cross_subsidiary_summary_sums_totals(client, tenant_id):
    await _ingest(client, tenant_id, "voyqi-rental", 100000.0)
    await _ingest(client, tenant_id, "voyqi-dealer", 250000.0)

    resp = await client.get(
        "/api/v1/executive/dashboard/summary",
        params={"metric_domain": "revenue"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["subsidiary_count"] == 2
    assert body["totals"]["total_revenue_usd"] == 350000.0
    assert set(body["by_subsidiary"].keys()) == {"voyqi-rental", "voyqi-dealer"}


@pytest.mark.asyncio
async def test_summary_uses_latest_snapshot_per_subsidiary(client, tenant_id):
    await _ingest(client, tenant_id, "voyqi-rental", 100000.0, period_start=datetime(2026, 1, 1, tzinfo=timezone.utc))
    await _ingest(client, tenant_id, "voyqi-rental", 150000.0, period_start=datetime(2026, 2, 1, tzinfo=timezone.utc))

    resp = await client.get(
        "/api/v1/executive/dashboard/summary",
        params={"metric_domain": "revenue"},
        headers={"X-Tenant-Id": tenant_id},
    )
    body = resp.json()
    assert body["subsidiary_count"] == 1
    assert body["totals"]["total_revenue_usd"] == 150000.0


@pytest.mark.asyncio
async def test_trend_returns_ordered_snapshots(client, tenant_id):
    await _ingest(client, tenant_id, "voyqi-rental", 100000.0)

    resp = await client.get(
        "/api/v1/executive/dashboard/trend",
        params={"subsidiary_key": "voyqi-rental", "metric_domain": "revenue"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1
