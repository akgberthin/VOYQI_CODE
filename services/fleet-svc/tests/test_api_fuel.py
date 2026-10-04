import uuid

from tests.conftest import TENANT_HEADERS


async def test_full_fuel_flow_api(client):
    vehicle_id = str(uuid.uuid4())
    await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "gasoline", "quantity": 12.0, "cost_usd": 45.0, "odometer_miles": 20000}, headers=TENANT_HEADERS)
    await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "gasoline", "quantity": 11.5, "cost_usd": 43.0, "odometer_miles": 20400}, headers=TENANT_HEADERS)

    entries_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/fuel-entries", headers=TENANT_HEADERS)
    assert len(entries_resp.json()) == 2

    summary_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/fuel-summary", headers=TENANT_HEADERS)
    assert summary_resp.json()["total_cost_usd"] == 88.0
    assert summary_resp.json()["cost_per_mile_usd"] == round(88.0 / 400, 4)


async def test_fuel_summary_electric_vehicle_api(client):
    vehicle_id = str(uuid.uuid4())
    await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "electric", "quantity": 40.0, "cost_usd": 12.0, "odometer_miles": 10000}, headers=TENANT_HEADERS)
    await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "electric", "quantity": 38.0, "cost_usd": 11.5, "odometer_miles": 10150}, headers=TENANT_HEADERS)

    summary_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/fuel-summary", headers=TENANT_HEADERS)
    assert summary_resp.json()["efficiency"] == round(150 / 38.0, 2)


async def test_fuel_summary_no_entries_api(client):
    resp = await client.get(f"/api/v1/fleet/vehicles/{uuid.uuid4()}/fuel-summary", headers=TENANT_HEADERS)
    assert resp.json()["entry_count"] == 0
    assert resp.json()["cost_per_mile_usd"] is None


async def test_second_fuel_entry_publishes_anomaly_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import fuel
    monkeypatch.setattr(fuel, "publish_event", spy)

    vehicle_id = str(uuid.uuid4())
    resp1 = await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "gasoline", "quantity": 12.0, "cost_usd": 45.0, "odometer_miles": 20000}, headers=TENANT_HEADERS)
    assert len(published) == 0  # first entry has nothing to compare against

    await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "gasoline", "quantity": 11.5, "cost_usd": 43.0, "odometer_miles": 20400}, headers=TENANT_HEADERS)
    event_type, payload = published[0]
    assert event_type == "flt.fuel_anomaly_requested"
    assert payload["implied_efficiency"] == round(400 / 11.5, 2)
    assert resp1.status_code == 201


async def test_assess_fuel_anomaly_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_fuel_anomaly", lambda tenant_id, vehicle_id, entry_id, energy_type, eff, cost: {"assessment": "flagged", "reasoning": "x"})

    vehicle_id = str(uuid.uuid4())
    await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "gasoline", "quantity": 12.0, "cost_usd": 45.0, "odometer_miles": 20000}, headers=TENANT_HEADERS)
    second_resp = await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "gasoline", "quantity": 11.5, "cost_usd": 43.0, "odometer_miles": 20400}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/fleet/fuel-entries/{second_resp.json()['id']}/assess-anomaly-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["assessment"] == "flagged"


async def test_assess_fuel_anomaly_now_first_entry_409(client):
    vehicle_id = str(uuid.uuid4())
    first_resp = await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "gasoline", "quantity": 12.0, "cost_usd": 45.0, "odometer_miles": 20000}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/fleet/fuel-entries/{first_resp.json()['id']}/assess-anomaly-now", headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_create_and_get_fuel_anomaly_api(client):
    vehicle_id = str(uuid.uuid4())
    entry_resp = await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "gasoline", "quantity": 12.0, "cost_usd": 45.0, "odometer_miles": 20000}, headers=TENANT_HEADERS)

    create_resp = await client.post(
        f"/api/v1/fleet/fuel-entries/{entry_resp.json()['id']}/anomaly-assessments", json={"vehicle_id": vehicle_id, "assessment": "flagged", "reasoning": "implausible efficiency"}, headers=TENANT_HEADERS
    )
    assert create_resp.status_code == 200

    get_resp = await client.get(f"/api/v1/fleet/fuel-entries/{entry_resp.json()['id']}/anomaly-assessment", headers=TENANT_HEADERS)
    assert get_resp.json()["assessment"] == "flagged"


async def test_get_fuel_anomaly_unknown_entry_404(client):
    resp = await client.get(f"/api/v1/fleet/fuel-entries/{uuid.uuid4()}/anomaly-assessment", headers=TENANT_HEADERS)
    assert resp.status_code == 404
