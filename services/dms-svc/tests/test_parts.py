from tests.conftest import TENANT_HEADERS


async def test_low_stock_part_auto_reordered(client):
    part = (await client.post("/api/v1/dms/parts", json={
        "part_number": "BP-1001", "name": "Brake Pads", "unit_cost_usd": 45, "stock_qty": 2, "reorder_point": 5,
    }, headers=TENANT_HEADERS)).json()
    resp = await client.post("/api/v1/dms/parts/evaluate-reorder", headers=TENANT_HEADERS)
    reordered = resp.json()
    assert len(reordered) == 1
    assert reordered[0]["id"] == part["id"]
    assert reordered[0]["stock_qty"] > reordered[0]["reorder_point"]
    assert reordered[0]["stock_qty"] == 2 + max(20, 5 * 3)


async def test_healthy_stock_part_not_reordered(client):
    await client.post("/api/v1/dms/parts", json={
        "part_number": "OF-2001", "name": "Oil Filter", "unit_cost_usd": 12, "stock_qty": 50, "reorder_point": 10,
    }, headers=TENANT_HEADERS)
    resp = await client.post("/api/v1/dms/parts/evaluate-reorder", headers=TENANT_HEADERS)
    assert resp.json() == []


async def test_reorder_does_not_repeat_at_same_stock_level(client):
    await client.post("/api/v1/dms/parts", json={
        "part_number": "TR-3005", "name": "Transmission Fluid", "unit_cost_usd": 18, "stock_qty": 3, "reorder_point": 8,
    }, headers=TENANT_HEADERS)
    first = (await client.post("/api/v1/dms/parts/evaluate-reorder", headers=TENANT_HEADERS)).json()
    assert len(first) == 1
    second = (await client.post("/api/v1/dms/parts/evaluate-reorder", headers=TENANT_HEADERS)).json()
    assert second == []
