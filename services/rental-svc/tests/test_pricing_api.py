from tests.conftest import TENANT_HEADERS


async def _setup_hub(client, hub_id, rental_class, count, rate):
    for i in range(count):
        await client.post("/api/v1/rental/vehicles", json={"hub_id": hub_id, "rental_class": rental_class, "vin": f"{hub_id}-{rental_class}-{i:03d}", "base_daily_rate_usd": rate}, headers=TENANT_HEADERS)


async def _rent_out(client, hub_id, rental_class, count):
    vehicles = (await client.get("/api/v1/rental/vehicles", params={"hub_id": hub_id, "rental_class": rental_class}, headers=TENANT_HEADERS)).json()
    for v in vehicles[:count]:
        await client.patch(f"/api/v1/rental/vehicles/{v['id']}/status", json={"status": "rented"}, headers=TENANT_HEADERS)


async def test_recommendation_is_read_only(client):
    await _setup_hub(client, "hub-a", "urban", 5, 60)
    res = (await client.post("/api/v1/rental/reservations", json={"hub_id": "hub-a", "rental_class": "urban", "customer_name": "Jane", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": 60}, headers=TENANT_HEADERS)).json()

    rec1 = (await client.get(f"/api/v1/rental/pricing/reservations/{res['id']}/recommendation", headers=TENANT_HEADERS)).json()
    reservation_after = (await client.get(f"/api/v1/rental/reservations/{res['id']}", headers=TENANT_HEADERS)).json()
    assert reservation_after["daily_rate_usd"] == 60  # recommend_rate never writes anything
    assert rec1["current_rate_usd"] == 60


async def test_apply_recommendation_applies_and_logs_when_meaningful(client):
    await _setup_hub(client, "hub-b", "urban", 5, 60)
    await _rent_out(client, "hub-b", "urban", 4)  # 80% utilization
    res = (await client.post("/api/v1/rental/reservations", json={"hub_id": "hub-b", "rental_class": "urban", "customer_name": "Jane", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": 60}, headers=TENANT_HEADERS)).json()
    # Simulate demand by booking several reservations in quick succession
    for i in range(5):
        await client.post("/api/v1/rental/reservations", json={"hub_id": "hub-b", "rental_class": "urban", "customer_name": f"Demand{i}", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": 60}, headers=TENANT_HEADERS)

    applied = (await client.post(f"/api/v1/rental/pricing/reservations/{res['id']}/apply-recommendation", json={}, headers=TENANT_HEADERS)).json()
    assert applied["applied"] is True
    assert applied["log_id"] is not None

    reservation_after = (await client.get(f"/api/v1/rental/reservations/{res['id']}", headers=TENANT_HEADERS)).json()
    assert reservation_after["daily_rate_usd"] == applied["recommendation"]["recommended_rate_usd"]

    logs = (await client.get("/api/v1/rental/pricing/logs", params={"reservation_id": res["id"]}, headers=TENANT_HEADERS)).json()
    assert len(logs) == 1
    assert logs[0]["reasoning"]  # the full breakdown, not just a number


async def test_apply_recommendation_skips_negligible_change(client):
    await _setup_hub(client, "hub-c", "urban", 10, 60)
    await _rent_out(client, "hub-c", "urban", 5)  # 50% utilization — squarely in the neutral 40-80% band
    res = (await client.post("/api/v1/rental/reservations", json={"hub_id": "hub-c", "rental_class": "urban", "customer_name": "Jane", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": 60}, headers=TENANT_HEADERS)).json()
    # A single isolated booking, below the zero-baseline velocity
    # floor, stays classified as STEADY rather than ACCELERATING.

    applied = (await client.post(f"/api/v1/rental/pricing/reservations/{res['id']}/apply-recommendation", json={}, headers=TENANT_HEADERS)).json()
    assert applied["applied"] is False
    assert applied["log_id"] is None

    reservation_after = (await client.get(f"/api/v1/rental/reservations/{res['id']}", headers=TENANT_HEADERS)).json()
    assert reservation_after["daily_rate_usd"] == 60  # unchanged
