from tests.conftest import TENANT_HEADERS


async def _setup_deal(client, price_usd=20000, cost_usd=17000, vin="VIN00000000000010"):
    rt = (await client.post("/api/v1/dms/rooftops", json={"name": "RT1", "oem_brand": "Toyota", "license_number": "L1", "license_expiry": "2027-01-01T00:00:00Z"}, headers=TENANT_HEADERS)).json()
    v = (await client.post("/api/v1/dms/vehicles", json={"rooftop_id": rt["id"], "vin": vin, "description": "Car", "condition": "used", "price_usd": price_usd, "cost_usd": cost_usd}, headers=TENANT_HEADERS)).json()
    d = (await client.post("/api/v1/dms/deals", json={"rooftop_id": rt["id"], "vehicle_id": v["id"], "buyer_name": "Jane"}, headers=TENANT_HEADERS)).json()
    return rt, v, d


async def test_list_deals_returns_all_for_tenant(client):
    _rt1, _v1, d1 = await _setup_deal(client, vin="LISTVIN00000001")
    _rt2, _v2, d2 = await _setup_deal(client, vin="LISTVIN00000002")
    listed = (await client.get("/api/v1/dms/deals", headers=TENANT_HEADERS)).json()
    ids = {d["id"] for d in listed}
    assert d1["id"] in ids and d2["id"] in ids


async def test_desk_and_approve_clears_gross_floor_normally(client):
    rt, v, d = await _setup_deal(client, price_usd=20000, cost_usd=17000)
    resp = await client.post(f"/api/v1/dms/deals/{d['id']}/desk-and-approve", headers=TENANT_HEADERS)
    body = resp.json()
    assert body["status"] == "approved"  # no intermediate "desking" state to act on
    desk_price = round(20000 * 0.97, 2)
    assert body["price_usd"] == desk_price
    gross_pct = (desk_price - 17000) / 17000
    assert gross_pct >= 0.03


async def test_desk_and_approve_auto_counters_below_floor_price(client):
    # cost=19900, price=20000 -> desk price 19400, which is BELOW cost -> must be auto-countered up to the floor, not rejected.
    rt, v, d = await _setup_deal(client, price_usd=20000, cost_usd=19900)
    resp = await client.post(f"/api/v1/dms/deals/{d['id']}/desk-and-approve", headers=TENANT_HEADERS)
    body = resp.json()
    assert body["status"] == "approved"  # auto-countered AND approved in the same pass, never rejected
    assert body["price_usd"] >= round(19900 * 1.03, 2)


async def test_deliver_deal_marks_vehicle_sold(client):
    rt, v, d = await _setup_deal(client)
    await client.post(f"/api/v1/dms/deals/{d['id']}/desk-and-approve", headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/dms/deals/{d['id']}/deliver", headers=TENANT_HEADERS)
    assert resp.json()["status"] == "delivered"
    vehicle_resp = await client.get(f"/api/v1/dms/vehicles/{v['id']}", headers=TENANT_HEADERS)
    assert vehicle_resp.json()["status"] == "sold"
