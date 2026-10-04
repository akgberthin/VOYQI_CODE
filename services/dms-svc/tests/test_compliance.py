from tests.conftest import TENANT_HEADERS


async def test_stolen_vehicle_hold_appears_and_resolves(client):
    await client.post("/api/v1/dms/appraisals/find-or-create", json={
        "vin": "COMPVIN001", "mileage": 20000, "stolen_vehicle_match": True,
    }, headers=TENANT_HEADERS)
    holds = (await client.get("/api/v1/dms/compliance-holds", headers=TENANT_HEADERS)).json()
    assert len(holds) == 1

    resp = await client.post(f"/api/v1/dms/compliance-holds/{holds[0]['id']}/resolve", headers=TENANT_HEADERS)
    assert resp.json()["resolved"] is True


async def test_non_security_actions_never_create_a_hold(client):
    """Title disclosure, franchise remediation, adverse action, and
    parts reorder all execute autonomously and never touch this
    queue — confirming it stays exclusively a security-hold list."""
    rt = (await client.post("/api/v1/dms/rooftops", json={"name": "RT1", "oem_brand": "Toyota", "license_number": "L1", "license_expiry": "2027-01-01T00:00:00Z"}, headers=TENANT_HEADERS)).json()
    v = (await client.post("/api/v1/dms/vehicles", json={"rooftop_id": rt["id"], "vin": "NOHOLDVIN01", "description": "x", "condition": "used", "price_usd": 20000, "cost_usd": 17000}, headers=TENANT_HEADERS)).json()
    await client.post(f"/api/v1/dms/vehicles/{v['id']}/vehicle-history-report", json={"provider": "carfax", "title_status": "salvage", "accident_count": 1, "odometer_rollback": False}, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/fi/adverse-action-notices", json={"deal_id": str(rt["id"]), "reason": "test"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/franchise", json={"rooftop_id": rt["id"], "status": "non_compliant", "score": 50}, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/franchise/evaluate-remediation", headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/parts", json={"part_number": "P1", "name": "Part", "unit_cost_usd": 10, "stock_qty": 1, "reorder_point": 5}, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/parts/evaluate-reorder", headers=TENANT_HEADERS)

    holds = (await client.get("/api/v1/dms/compliance-holds", headers=TENANT_HEADERS)).json()
    assert holds == []


async def test_resolve_nonexistent_hold_returns_404(client):
    import uuid
    resp = await client.post(f"/api/v1/dms/compliance-holds/{uuid.uuid4()}/resolve", headers=TENANT_HEADERS)
    assert resp.status_code == 404
