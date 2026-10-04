from tests.conftest import TENANT_HEADERS


async def _rooftop(client):
    resp = await client.post("/api/v1/dms/rooftops", json={"name": "RT1", "oem_brand": "Toyota", "license_number": "L1", "license_expiry": "2027-01-01T00:00:00Z"}, headers=TENANT_HEADERS)
    return resp.json()["id"]


async def test_list_and_get_repair_orders(client):
    rooftop_id = await _rooftop(client)
    created = (await client.post("/api/v1/dms/service/repair-orders", json={
        "rooftop_id": rooftop_id, "vin": "LISTSVCVIN01", "customer_name": "Alice", "description": "tire rotation", "warranty": False,
    }, headers=TENANT_HEADERS)).json()

    listed = (await client.get("/api/v1/dms/service/repair-orders", headers=TENANT_HEADERS)).json()
    assert any(r["id"] == created["id"] for r in listed)

    fetched = (await client.get(f"/api/v1/dms/service/repair-orders/{created['id']}", headers=TENANT_HEADERS)).json()
    assert fetched["id"] == created["id"]


async def test_ro_auto_dispatched_on_creation(client):
    rooftop_id = await _rooftop(client)
    resp = await client.post("/api/v1/dms/service/repair-orders", json={
        "rooftop_id": rooftop_id, "vin": "SVCVIN001", "customer_name": "Bob", "description": "oil change", "warranty": False,
    }, headers=TENANT_HEADERS)
    body = resp.json()
    assert body["tech"] != ""  # never left "open, awaiting dispatch"
    assert body["status"] == "in_progress"


async def test_duration_estimate_from_keywords(client):
    rooftop_id = await _rooftop(client)
    cases = [("transmission rebuild", 3), ("recall software update", 1), ("brake pad replacement", 1), ("oil change and tire rotation", 0), ("mystery noise from dashboard", 1)]
    for desc, expected_days in cases:
        resp = await client.post("/api/v1/dms/service/repair-orders", json={
            "rooftop_id": rooftop_id, "vin": "SVCVIN002", "customer_name": "Bob", "description": desc, "warranty": False,
        }, headers=TENANT_HEADERS)
        assert resp.json()["estimated_duration_days"] == expected_days, desc


async def test_multiday_job_autochains_loaner_assignment(client):
    rooftop_id = await _rooftop(client)
    await client.post("/api/v1/dms/service/repair-orders", json={
        "rooftop_id": rooftop_id, "vin": "SVCVIN003", "customer_name": "Transmission Customer", "description": "transmission rebuild", "warranty": False,
    }, headers=TENANT_HEADERS)
    loaners = (await client.get("/api/v1/dms/loaners", headers=TENANT_HEADERS)).json()
    assert any(l["customer_name"] == "Transmission Customer" for l in loaners)


async def test_zero_day_job_does_not_trigger_loaner(client):
    rooftop_id = await _rooftop(client)
    await client.post("/api/v1/dms/service/repair-orders", json={
        "rooftop_id": rooftop_id, "vin": "SVCVIN004", "customer_name": "Oil Change Customer", "description": "oil change", "warranty": False,
    }, headers=TENANT_HEADERS)
    loaners = (await client.get("/api/v1/dms/loaners", headers=TENANT_HEADERS)).json()
    assert not any(l["customer_name"] == "Oil Change Customer" for l in loaners)


async def test_warranty_ro_status_and_zero_revenue_on_complete(client):
    rooftop_id = await _rooftop(client)
    ro = (await client.post("/api/v1/dms/service/repair-orders", json={
        "rooftop_id": rooftop_id, "vin": "SVCVIN005", "customer_name": "Warranty Customer", "description": "recall fix", "warranty": True,
    }, headers=TENANT_HEADERS)).json()
    assert ro["status"] == "warranty_claim"
    completed = (await client.post(f"/api/v1/dms/service/repair-orders/{ro['id']}/complete", headers=TENANT_HEADERS)).json()
    assert completed["revenue_usd"] == 0.0
