from tests.conftest import TENANT_HEADERS


async def _create_vehicle(client, hub_id, rental_class, vin, rate=60):
    return (await client.post("/api/v1/rental/vehicles", json={"hub_id": hub_id, "rental_class": rental_class, "vin": vin, "base_daily_rate_usd": rate}, headers=TENANT_HEADERS)).json()


async def test_admin_capacity_risk_insight_generated_at_high_forecast(client):
    for i in range(2):
        await _create_vehicle(client, "hub-risk", "urban", f"RISKVIN{i:03d}")
    for i in range(15):
        await client.post("/api/v1/rental/reservations", json={"hub_id": "hub-risk", "rental_class": "urban", "customer_name": f"C{i}", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": 60}, headers=TENANT_HEADERS)
    insights = (await client.post("/api/v1/rental/insights/admin/generate", params={"hub_id": "hub-risk", "rental_class": "urban"}, headers=TENANT_HEADERS)).json()
    assert any(i["insight_type"] in ("capacity_risk", "demand_spike") for i in insights)


async def test_admin_underutilized_insight_generated_at_low_utilization(client):
    for i in range(10):
        await _create_vehicle(client, "hub-quiet-admin", "urban", f"QVIN{i:03d}")
    insights = (await client.post("/api/v1/rental/insights/admin/generate", params={"hub_id": "hub-quiet-admin", "rental_class": "urban"}, headers=TENANT_HEADERS)).json()
    assert any(i["insight_type"] == "underutilized" for i in insights)


async def test_resolve_admin_insight(client):
    for i in range(10):
        await _create_vehicle(client, "hub-resolve", "urban", f"RVIN{i:03d}")
    insights = (await client.post("/api/v1/rental/insights/admin/generate", params={"hub_id": "hub-resolve", "rental_class": "urban"}, headers=TENANT_HEADERS)).json()
    assert insights
    resolved = (await client.post(f"/api/v1/rental/insights/admin/{insights[0]['id']}/resolve", headers=TENANT_HEADERS)).json()
    assert resolved["resolved"] is True


async def test_client_insight_low_availability_generated(client):
    for i in range(2):
        await _create_vehicle(client, "hub-client", "urban", f"CLVIN{i:03d}")
    for i in range(10):
        await client.post("/api/v1/rental/reservations", json={"hub_id": "hub-client", "rental_class": "urban", "customer_name": f"C{i}", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": 60}, headers=TENANT_HEADERS)
    insights = (await client.post("/api/v1/rental/insights/client/generate", params={"hub_id": "hub-client", "rental_class": "urban"}, headers=TENANT_HEADERS)).json()
    assert len(insights) > 0


async def test_reallocation_opportunity_found_for_lopsided_hubs(client):
    for i in range(2):
        v = await _create_vehicle(client, "hub-full", "family", f"FULLVIN{i:03d}")
        await client.patch(f"/api/v1/rental/vehicles/{v['id']}/status", json={"status": "rented"}, headers=TENANT_HEADERS)
    for i in range(5):
        await _create_vehicle(client, "hub-empty", "family", f"EMPTYVIN{i:03d}")

    opportunities = (await client.get("/api/v1/rental/hubs/reallocation-opportunities", params={"rental_class": "family"}, headers=TENANT_HEADERS)).json()
    assert len(opportunities) >= 1
    match = [o for o in opportunities if o["donor_hub_id"] == "hub-empty" and o["recipient_hub_id"] == "hub-full"]
    assert match
    assert match[0]["utilization_gap_pct"] >= 40


async def test_no_reallocation_opportunity_when_hubs_balanced(client):
    for i in range(3):
        await _create_vehicle(client, "hub-x", "luxury", f"XVIN{i:03d}")
    for i in range(3):
        await _create_vehicle(client, "hub-y", "luxury", f"YVIN{i:03d}")
    opportunities = (await client.get("/api/v1/rental/hubs/reallocation-opportunities", params={"rental_class": "luxury"}, headers=TENANT_HEADERS)).json()
    assert opportunities == []


async def test_execute_reallocation_moves_vehicle(client):
    v = await _create_vehicle(client, "hub-old", "adventure", "MOVEVIN001")
    moved = (await client.post("/api/v1/rental/reallocation/execute", json={"vehicle_id": v["id"], "new_hub_id": "hub-new"}, headers=TENANT_HEADERS)).json()
    assert moved["hub_id"] == "hub-new"
