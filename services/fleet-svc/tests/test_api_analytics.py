
from tests.conftest import TENANT_HEADERS


async def _certified_vehicle_api(client, vin="1HGCM82633A008888"):
    po_resp = await client.post(
        "/api/v1/fleet/purchase-orders", json={"source": "dealer", "vehicle_description": "test", "cost_usd": 42000.0, "created_by": "fleet-ops-1"}, headers=TENANT_HEADERS
    )
    vehicle_resp = await client.post(
        f"/api/v1/fleet/purchase-orders/{po_resp.json()['id']}/receive",
        json={"vin": vin, "make": "Test", "model": "Car", "year": 2024, "vehicle_class": "executive", "ownership_entity": "x"},
        headers=TENANT_HEADERS,
    )
    vehicle_id = vehicle_resp.json()["id"]
    await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/provision", json={"provisioned_by": "fleet-ops-1", "voyqi_card_id": "VQ-1"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/certify-class", json={"certified_by": "fleet-ops-1"}, headers=TENANT_HEADERS)
    return vehicle_id


async def test_fleet_summary_api(client):
    await _certified_vehicle_api(client, vin="1HGCM82633A007771")
    resp = await client.get("/api/v1/fleet/analytics/fleet-summary", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total_vehicles"] >= 1


async def test_vehicle_availability_api(client):
    vehicle_id = await _certified_vehicle_api(client, vin="1HGCM82633A007772")
    resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/availability", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["availability_pct"] == 100.0


async def test_vehicle_tco_api_no_schedule(client):
    vehicle_id = await _certified_vehicle_api(client, vin="1HGCM82633A007773")
    resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/tco", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total_tco_usd"] is None


async def test_vehicle_tco_api_with_full_data(client):
    vehicle_id = await _certified_vehicle_api(client, vin="1HGCM82633A007774")
    await client.post(
        "/api/v1/fleet/depreciation-schedules", json={"vehicle_id": vehicle_id, "acquisition_cost_usd": 42000.0, "salvage_value_usd": 12000.0, "useful_life_months": 60}, headers=TENANT_HEADERS
    )
    wo_resp = await client.post("/api/v1/fleet/work-orders", json={"vehicle_id": vehicle_id, "vendor": "VOYQI Certified Service", "created_by": "fleet-ops-1"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/fleet/work-orders/{wo_resp.json()['id']}/complete", params={"cost_usd": 350.0}, headers=TENANT_HEADERS)
    await client.post("/api/v1/fleet/fuel-entries", json={"vehicle_id": vehicle_id, "energy_type": "gasoline", "quantity": 12.0, "cost_usd": 45.0, "odometer_miles": 100}, headers=TENANT_HEADERS)

    resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/tco", headers=TENANT_HEADERS)
    assert resp.json()["maintenance_cost_usd"] == 350.0
    assert resp.json()["energy_cost_usd"] == 45.0
    assert resp.json()["total_tco_usd"] == 395.0


async def test_request_fleet_optimization_review_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import analytics
    monkeypatch.setattr(analytics, "publish_event", spy)

    vehicle_id = await _certified_vehicle_api(client, vin="1HGCM82633A007780")
    resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/request-fleet-optimization-review", headers=TENANT_HEADERS)
    assert resp.status_code == 202
    event_type, payload = published[0]
    assert event_type == "flt.fleet_optimization_requested"
    assert payload["vehicle_id"] == vehicle_id


async def test_review_fleet_optimization_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_fleet_optimization", lambda tenant_id, signals: {"recommendation": "retain", "reasoning": "x"})

    vehicle_id = await _certified_vehicle_api(client, vin="1HGCM82633A007781")
    resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/review-fleet-optimization-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["recommendation"] == "retain"


async def test_create_and_get_fleet_optimization_api(client):
    vehicle_id = await _certified_vehicle_api(client, vin="1HGCM82633A007782")

    create_resp = await client.post(
        f"/api/v1/fleet/vehicles/{vehicle_id}/fleet-optimization-assessments", json={"recommendation": "prioritize_disposition", "reasoning": "multiple signals converge"}, headers=TENANT_HEADERS
    )
    assert create_resp.status_code == 200

    get_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/fleet-optimization", headers=TENANT_HEADERS)
    assert get_resp.json()["recommendation"] == "prioritize_disposition"


async def test_get_fleet_optimization_unknown_vehicle_404(client):
    import uuid
    resp = await client.get(f"/api/v1/fleet/vehicles/{uuid.uuid4()}/fleet-optimization", headers=TENANT_HEADERS)
    assert resp.status_code == 404
