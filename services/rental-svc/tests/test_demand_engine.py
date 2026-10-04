from tests.conftest import TENANT_HEADERS


async def _create_vehicle(client, hub_id, rental_class, vin, rate=60):
    await client.post("/api/v1/rental/vehicles", json={"hub_id": hub_id, "rental_class": rental_class, "vin": vin, "base_daily_rate_usd": rate}, headers=TENANT_HEADERS)


async def test_single_isolated_booking_does_not_read_as_accelerating(client):
    """Regression: a zero prior-window baseline used to make ANY
    activity read as ACCELERATING, so one isolated booking after a
    quiet period would justify a demand premium on pure noise. A
    single booking must now classify as STEADY."""
    await client.post("/api/v1/rental/reservations", json={
        "hub_id": "hub-quiet", "rental_class": "urban", "customer_name": "Solo", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": 60,
    }, headers=TENANT_HEADERS)
    forecast = (await client.get("/api/v1/rental/hubs/hub-quiet/demand-forecast", params={"rental_class": "urban"}, headers=TENANT_HEADERS)).json()
    assert forecast["trend"] == "steady"


async def test_genuine_booking_surge_still_reads_as_accelerating(client):
    """The fix must not suppress real signal — enough bookings in a
    short window must still classify as ACCELERATING."""
    for i in range(10):
        await client.post("/api/v1/rental/reservations", json={
            "hub_id": "hub-busy", "rental_class": "urban", "customer_name": f"Surge{i}", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": 60,
        }, headers=TENANT_HEADERS)
    forecast = (await client.get("/api/v1/rental/hubs/hub-busy/demand-forecast", params={"rental_class": "urban"}, headers=TENANT_HEADERS)).json()
    assert forecast["trend"] == "accelerating"


async def test_forecast_projects_utilization_forward(client):
    await _create_vehicle(client, "hub-forecast", "urban", "FCVIN0001")
    await _create_vehicle(client, "hub-forecast", "urban", "FCVIN0002")
    for i in range(4):
        await client.post("/api/v1/rental/reservations", json={
            "hub_id": "hub-forecast", "rental_class": "urban", "customer_name": f"C{i}", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": 60,
        }, headers=TENANT_HEADERS)
    forecast = (await client.get("/api/v1/rental/hubs/hub-forecast/demand-forecast", params={"rental_class": "urban"}, headers=TENANT_HEADERS)).json()
    assert forecast["forecasted_utilization_pct"] >= 0
    assert "util" in forecast["reasoning"].lower()
