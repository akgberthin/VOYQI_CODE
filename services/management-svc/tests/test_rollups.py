from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_ingest_and_list_snapshot(client, tenant_id):
    payload = {
        "cost_center_key": "it-shared",
        "metric_domain": "spend",
        "period_start": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 1, 31, tzinfo=timezone.utc).isoformat(),
        "metrics": {"total_spend_usd": 42000.0, "ticket_count": 128},
        "source_service": "admin-tool",
    }
    resp = await client.post("/api/v1/management/rollups", json=payload, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201
    assert resp.json()["metrics"]["total_spend_usd"] == 42000.0

    resp = await client.get("/api/v1/management/rollups", headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_filters_by_cost_center(client, tenant_id):
    base = {
        "period_start": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        "period_end": datetime(2026, 1, 31, tzinfo=timezone.utc).isoformat(),
        "source_service": "admin-tool",
        "metric_domain": "spend",
    }
    await client.post(
        "/api/v1/management/rollups",
        json={**base, "cost_center_key": "it-shared", "metrics": {"total_spend_usd": 100.0}},
        headers={"X-Tenant-Id": tenant_id},
    )
    await client.post(
        "/api/v1/management/rollups",
        json={**base, "cost_center_key": "facilities-hq", "metrics": {"total_spend_usd": 200.0}},
        headers={"X-Tenant-Id": tenant_id},
    )

    resp = await client.get(
        "/api/v1/management/rollups",
        params={"cost_center_key": "facilities-hq"},
        headers={"X-Tenant-Id": tenant_id},
    )
    body = resp.json()
    assert len(body) == 1
    assert body[0]["cost_center_key"] == "facilities-hq"
