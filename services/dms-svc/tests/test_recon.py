from tests.conftest import TENANT_HEADERS


async def _setup_vehicle_and_order(client):
    rt = (await client.post("/api/v1/dms/rooftops", json={"name": "RT1", "oem_brand": "Toyota", "license_number": "L1", "license_expiry": "2027-01-01T00:00:00Z"}, headers=TENANT_HEADERS)).json()
    v = (await client.post("/api/v1/dms/vehicles", json={"rooftop_id": rt["id"], "vin": "RECONVIN01", "description": "Trade-in", "condition": "used", "price_usd": 15000, "cost_usd": 12000, "days_in_inventory": 5}, headers=TENANT_HEADERS)).json()
    order = (await client.post("/api/v1/dms/recon/orders", json={"vehicle_id": v["id"], "arrived_days_ago": 3}, headers=TENANT_HEADERS)).json()
    return v, order


async def test_list_recon_orders_returns_all_for_tenant(client):
    _v1, order1 = await _setup_vehicle_and_order(client)
    listed = (await client.get("/api/v1/dms/recon/orders", headers=TENANT_HEADERS)).json()
    assert any(o["id"] == order1["id"] for o in listed)


async def test_work_item_auto_assigned_never_unassigned(client):
    v, order = await _setup_vehicle_and_order(client)
    resp = await client.post(f"/api/v1/dms/recon/orders/{order['id']}/work-items", json={
        "category": "mechanical", "description": "brake pads", "cost_usd": 180,
    }, headers=TENANT_HEADERS)
    item = resp.json()
    assert item["assigned_to"] == "tech-14"
    assert item["assigned_to"] != "unassigned"


async def test_each_category_maps_to_a_real_assignee(client):
    v, order = await _setup_vehicle_and_order(client)
    for category, expected in [("mechanical", "tech-14"), ("detail", "detail-shop"), ("body", "body-shop-1"), ("tires", "tire-bay"), ("other", "shop-foreman")]:
        resp = await client.post(f"/api/v1/dms/recon/orders/{order['id']}/work-items", json={"category": category, "description": "x", "cost_usd": 0}, headers=TENANT_HEADERS)
        assert resp.json()["assigned_to"] == expected


async def test_order_auto_advances_to_front_line_ready_on_last_item_complete(client):
    v, order = await _setup_vehicle_and_order(client)
    item1 = (await client.post(f"/api/v1/dms/recon/orders/{order['id']}/work-items", json={"category": "mechanical", "description": "brakes", "cost_usd": 100}, headers=TENANT_HEADERS)).json()
    item2 = (await client.post(f"/api/v1/dms/recon/orders/{order['id']}/work-items", json={"category": "detail", "description": "wash", "cost_usd": 50}, headers=TENANT_HEADERS)).json()

    await client.post(f"/api/v1/dms/recon/work-items/{item1['id']}/complete", headers=TENANT_HEADERS)
    mid_check = (await client.get(f"/api/v1/dms/recon/orders/{order['id']}", headers=TENANT_HEADERS)).json()
    assert mid_check["status"] != "completed"  # one item still open

    await client.post(f"/api/v1/dms/recon/work-items/{item2['id']}/complete", headers=TENANT_HEADERS)
    final_check = (await client.get(f"/api/v1/dms/recon/orders/{order['id']}", headers=TENANT_HEADERS)).json()
    assert final_check["status"] == "completed"  # no "mark front-line ready" click needed
    assert final_check["turnaround_days"] == 3

    vehicle_resp = await client.get(f"/api/v1/dms/vehicles/{v['id']}", headers=TENANT_HEADERS)
    vehicle = vehicle_resp.json()
    assert vehicle["status"] == "in_stock"
    assert vehicle["days_in_inventory"] == 0  # aging clock reset
