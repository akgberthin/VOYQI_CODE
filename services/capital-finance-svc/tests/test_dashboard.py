from datetime import datetime, timezone

import pytest


async def _ingest(client, tenant_id, funding_source_key, balance):
    payload = {
        "funding_source_key": funding_source_key,
        "metric_domain": "balance",
        "period_start": datetime(2026, 2, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 2, 28, tzinfo=timezone.utc).isoformat(),
        "metrics": {"outstanding_balance_usd": balance},
        "source_service": "test",
    }
    resp = await client.post("/api/v1/capital-finance/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_cross_funding_source_summary_sums_totals(client, tenant_id):
    await _ingest(client, tenant_id, "term-loan-a", 5000000.0)
    await _ingest(client, tenant_id, "revolver-1", 1200000.0)

    resp = await client.get(
        "/api/v1/capital-finance/dashboard/summary",
        params={"metric_domain": "balance"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["funding_source_count"] == 2
    assert body["totals"]["outstanding_balance_usd"] == 6200000.0


@pytest.mark.asyncio
async def test_trend_returns_ordered_snapshots(client, tenant_id):
    await _ingest(client, tenant_id, "term-loan-a", 5000000.0)

    resp = await client.get(
        "/api/v1/capital-finance/dashboard/trend",
        params={"funding_source_key": "term-loan-a", "metric_domain": "balance"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1
