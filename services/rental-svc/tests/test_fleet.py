from tests.conftest import TENANT_HEADERS


async def test_create_and_get_vehicle(client):
    resp = await client.post("/api/v1/rental/vehicles", json={"hub_id": "hub-1", "rental_class": "executive", "vin": "EXECVIN001", "base_daily_rate_usd": 120}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    vehicle = resp.json()
    assert vehicle["status"] == "available"

    fetched = (await client.get(f"/api/v1/rental/vehicles/{vehicle['id']}", headers=TENANT_HEADERS)).json()
    assert fetched["vin"] == "EXECVIN001"


async def test_list_vehicles_filters_by_hub_class_status(client):
    await client.post("/api/v1/rental/vehicles", json={"hub_id": "hub-2", "rental_class": "luxury", "vin": "LUXVIN001", "base_daily_rate_usd": 200}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rental/vehicles", json={"hub_id": "hub-2", "rental_class": "urban", "vin": "URBVIN999", "base_daily_rate_usd": 60}, headers=TENANT_HEADERS)
    luxury_only = (await client.get("/api/v1/rental/vehicles", params={"hub_id": "hub-2", "rental_class": "luxury"}, headers=TENANT_HEADERS)).json()
    assert all(v["rental_class"] == "luxury" for v in luxury_only)


async def test_set_vehicle_status(client):
    vehicle = (await client.post("/api/v1/rental/vehicles", json={"hub_id": "hub-3", "rental_class": "performance", "vin": "PERFVIN001", "base_daily_rate_usd": 300}, headers=TENANT_HEADERS)).json()
    updated = (await client.patch(f"/api/v1/rental/vehicles/{vehicle['id']}/status", json={"status": "maintenance"}, headers=TENANT_HEADERS)).json()
    assert updated["status"] == "maintenance"


async def test_get_nonexistent_vehicle_404(client):
    import uuid
    resp = await client.get(f"/api/v1/rental/vehicles/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404
