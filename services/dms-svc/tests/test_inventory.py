from tests.conftest import TENANT_HEADERS


async def _create_rooftop(client):
    resp = await client.post("/api/v1/dms/rooftops", json={
        "name": "RT1", "oem_brand": "Toyota", "license_number": "L1", "license_expiry": "2027-01-01T00:00:00Z",
    }, headers=TENANT_HEADERS)
    return resp.json()["id"]


async def _create_vehicle(client, rooftop_id, **overrides):
    body = {
        "rooftop_id": rooftop_id, "vin": "VIN00000000000001", "description": "Test Car",
        "condition": "used", "price_usd": 20000, "cost_usd": 17000, "days_in_inventory": 0,
    }
    body.update(overrides)
    resp = await client.post("/api/v1/dms/vehicles", json=body, headers=TENANT_HEADERS)
    return resp.json()


async def test_apply_price_writes_directly_no_acceptance_step(client):
    rooftop_id = await _create_rooftop(client)
    vehicle = await _create_vehicle(client, rooftop_id)
    resp = await client.post(f"/api/v1/dms/vehicles/{vehicle['id']}/price", json={"price_usd": 18500, "reasoning": "test"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["price_usd"] == 18500


async def test_aging_sweep_marks_down_vehicle_past_threshold(client):
    rooftop_id = await _create_rooftop(client)
    vehicle = await _create_vehicle(client, rooftop_id, vin="VIN00000000000002", days_in_inventory=74, price_usd=68000)
    resp = await client.post("/api/v1/dms/vehicles/evaluate-aging-sweep", headers=TENANT_HEADERS)
    swept = resp.json()
    assert len(swept) == 1
    assert swept[0]["price_usd"] < 68000


async def test_aging_sweep_does_not_touch_vehicle_below_threshold(client):
    rooftop_id = await _create_rooftop(client)
    await _create_vehicle(client, rooftop_id, vin="VIN00000000000003", days_in_inventory=10, price_usd=45000)
    resp = await client.post("/api/v1/dms/vehicles/evaluate-aging-sweep", headers=TENANT_HEADERS)
    assert resp.json() == []


async def test_aging_sweep_does_not_compound_at_same_days_value(client):
    rooftop_id = await _create_rooftop(client)
    await _create_vehicle(client, rooftop_id, vin="VIN00000000000004", days_in_inventory=74, price_usd=68000)
    await client.post("/api/v1/dms/vehicles/evaluate-aging-sweep", headers=TENANT_HEADERS)
    resp2 = await client.post("/api/v1/dms/vehicles/evaluate-aging-sweep", headers=TENANT_HEADERS)
    assert resp2.json() == []  # nothing re-marked-down at the same aging value


async def test_book_value_quote_feeds_aging_sweep_anchor(client):
    rooftop_id = await _create_rooftop(client)
    vehicle = await _create_vehicle(client, rooftop_id, vin="VIN00000000000005", days_in_inventory=65, price_usd=30000)
    await client.post("/api/v1/dms/vehicles/book-value-quotes", json={"vin": "VIN00000000000005", "source": "kbb", "wholesale_usd": 24000, "retail_usd": 26000}, headers=TENANT_HEADERS)

    avg_resp = await client.get("/api/v1/dms/vehicles/book-value-quotes/VIN00000000000005/average", headers=TENANT_HEADERS)
    assert avg_resp.json()["average_retail_value_usd"] == 26000

    swept = (await client.post("/api/v1/dms/vehicles/evaluate-aging-sweep", headers=TENANT_HEADERS)).json()
    assert len(swept) == 1
    assert swept[0]["price_usd"] < 26000  # anchored off book value, not the (higher) asking price


async def test_book_value_average_none_when_no_quotes(client):
    resp = await client.get("/api/v1/dms/vehicles/book-value-quotes/NOQUOTESVIN/average", headers=TENANT_HEADERS)
    assert resp.json() == {"average_retail_value_usd": None, "average_wholesale_value_usd": None}


async def test_vhr_pull_autonomously_drafts_disclosure_and_syncs_clean_title(client):
    rooftop_id = await _create_rooftop(client)
    vehicle = await _create_vehicle(client, rooftop_id, vin="VIN00000000000006")
    resp = await client.post(f"/api/v1/dms/vehicles/{vehicle['id']}/vehicle-history-report", json={
        "provider": "carfax", "title_status": "clean", "accident_count": 0, "odometer_rollback": False,
    }, headers=TENANT_HEADERS)
    body = resp.json()
    assert body["listing_synced"] is True
    assert body["disclosure_text"] == ""  # no issue, nothing to disclose


async def test_vhr_pull_autonomously_drafts_disclosure_for_salvage_title(client):
    rooftop_id = await _create_rooftop(client)
    vehicle = await _create_vehicle(client, rooftop_id, vin="VIN00000000000007")
    resp = await client.post(f"/api/v1/dms/vehicles/{vehicle['id']}/vehicle-history-report", json={
        "provider": "carfax", "title_status": "salvage", "accident_count": 2, "odometer_rollback": False,
    }, headers=TENANT_HEADERS)
    body = resp.json()
    assert body["listing_synced"] is True  # synced immediately, no human-typed override required
    assert "salvage title" in body["disclosure_text"]
