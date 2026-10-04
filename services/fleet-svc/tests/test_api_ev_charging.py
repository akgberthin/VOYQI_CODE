import uuid

from tests.conftest import TENANT_HEADERS


async def test_full_charging_session_flow_api(client):
    vehicle_id = str(uuid.uuid4())
    start_resp = await client.post("/api/v1/fleet/charging-sessions", json={"vehicle_id": vehicle_id, "station_location": "Miami Beach Hub — Bay 3"}, headers=TENANT_HEADERS)
    assert start_resp.status_code == 201
    session_id = start_resp.json()["id"]

    complete_resp = await client.post(f"/api/v1/fleet/charging-sessions/{session_id}/complete", json={"energy_added_kwh": 42.5, "cost_usd": 12.75}, headers=TENANT_HEADERS)
    assert complete_resp.json()["status"] == "completed"

    summary_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/charging-summary", headers=TENANT_HEADERS)
    assert summary_resp.json()["total_kwh"] == 42.5
    assert summary_resp.json()["total_cost_usd"] == 12.75


async def test_cannot_complete_charging_session_twice_api(client):
    vehicle_id = str(uuid.uuid4())
    start_resp = await client.post("/api/v1/fleet/charging-sessions", json={"vehicle_id": vehicle_id}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/fleet/charging-sessions/{start_resp.json()['id']}/complete", json={"energy_added_kwh": 30.0, "cost_usd": 9.0}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/fleet/charging-sessions/{start_resp.json()['id']}/complete", json={"energy_added_kwh": 30.0, "cost_usd": 9.0}, headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_battery_health_flow_api(client):
    vehicle_id = str(uuid.uuid4())
    resp = await client.post("/api/v1/fleet/battery-health", json={"vehicle_id": vehicle_id, "state_of_charge_pct": 72.0, "state_of_health_pct": 91.0}, headers=TENANT_HEADERS)
    assert resp.status_code == 201

    latest_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/battery-health/latest", headers=TENANT_HEADERS)
    assert latest_resp.json()["state_of_health_pct"] == 91.0


async def test_battery_degradation_watchlist_api(client):
    vehicle_id = str(uuid.uuid4())
    await client.post("/api/v1/fleet/battery-health", json={"vehicle_id": vehicle_id, "state_of_charge_pct": 50.0, "state_of_health_pct": 74.0}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/fleet/battery-health/watchlist", headers=TENANT_HEADERS)
    assert any(r["vehicle_id"] == vehicle_id for r in resp.json())


async def test_second_battery_reading_publishes_trend_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import ev_charging
    monkeypatch.setattr(ev_charging, "publish_event", spy)

    vehicle_id = str(uuid.uuid4())
    resp1 = await client.post("/api/v1/fleet/battery-health", json={"vehicle_id": vehicle_id, "state_of_charge_pct": 80.0, "state_of_health_pct": 95.0}, headers=TENANT_HEADERS)
    assert len(published) == 0  # first reading has nothing to trend against

    await client.post("/api/v1/fleet/battery-health", json={"vehicle_id": vehicle_id, "state_of_charge_pct": 75.0, "state_of_health_pct": 92.0}, headers=TENANT_HEADERS)
    event_type, payload = published[0]
    assert event_type == "flt.battery_degradation_trend_requested"
    assert payload["first_soh_pct"] == 95.0
    assert payload["latest_soh_pct"] == 92.0
    assert resp1.status_code == 201


async def test_forecast_battery_degradation_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_battery_degradation_trend", lambda tenant_id, vehicle_id, first, latest, days, threshold: {"estimated_days_to_threshold": 240, "reasoning": "x"})

    vehicle_id = str(uuid.uuid4())
    await client.post("/api/v1/fleet/battery-health", json={"vehicle_id": vehicle_id, "state_of_charge_pct": 80.0, "state_of_health_pct": 95.0}, headers=TENANT_HEADERS)
    await client.post("/api/v1/fleet/battery-health", json={"vehicle_id": vehicle_id, "state_of_charge_pct": 75.0, "state_of_health_pct": 92.0}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/forecast-battery-degradation-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["estimated_days_to_threshold"] == 240


async def test_forecast_battery_degradation_now_single_reading_409(client):
    vehicle_id = str(uuid.uuid4())
    await client.post("/api/v1/fleet/battery-health", json={"vehicle_id": vehicle_id, "state_of_charge_pct": 80.0, "state_of_health_pct": 95.0}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/forecast-battery-degradation-now", headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_create_and_get_battery_degradation_forecast_api(client):
    vehicle_id = str(uuid.uuid4())
    create_resp = await client.post(
        f"/api/v1/fleet/vehicles/{vehicle_id}/battery-degradation-forecasts", json={"degradation_rate_pct_per_day": -0.05, "estimated_days_to_threshold": 240, "reasoning": "steady decline"}, headers=TENANT_HEADERS
    )
    assert create_resp.status_code == 200

    get_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/battery-degradation-forecast", headers=TENANT_HEADERS)
    assert get_resp.json()["estimated_days_to_threshold"] == 240


async def test_get_battery_degradation_forecast_unknown_vehicle_404(client):
    resp = await client.get(f"/api/v1/fleet/vehicles/{uuid.uuid4()}/battery-degradation-forecast", headers=TENANT_HEADERS)
    assert resp.status_code == 404
