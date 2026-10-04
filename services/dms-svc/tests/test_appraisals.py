from tests.conftest import TENANT_HEADERS


async def test_list_appraisals_returns_all_for_tenant(client):
    a1 = (await client.post("/api/v1/dms/appraisals/find-or-create", json={"vin": "LISTVIN001", "mileage": 30000}, headers=TENANT_HEADERS)).json()
    a2 = (await client.post("/api/v1/dms/appraisals/find-or-create", json={"vin": "LISTVIN002", "mileage": 25000}, headers=TENANT_HEADERS)).json()
    listed = (await client.get("/api/v1/dms/appraisals", headers=TENANT_HEADERS)).json()
    ids = {a["id"] for a in listed}
    assert a1["id"] in ids and a2["id"] in ids


async def test_clean_vin_valuation_and_offer_apply_in_same_call(client):
    appraisal = (await client.post("/api/v1/dms/appraisals/find-or-create", json={
        "vin": "CLEANVIN001", "mileage": 40000,
    }, headers=TENANT_HEADERS)).json()
    assert appraisal["status"] == "pending"

    resp = await client.post(f"/api/v1/dms/appraisals/{appraisal['id']}/valuation-and-offer", json={
        "valuation_usd": 15000,
    }, headers=TENANT_HEADERS)
    body = resp.json()
    assert body["status"] == "offered"  # no separate acceptance step to produce the offer
    assert body["valuation_usd"] == 15000
    assert body["offer_usd"] == round(15000 * 0.93, 2)


async def test_stolen_vehicle_match_blocks_with_no_valuation_or_offer(client):
    appraisal = (await client.post("/api/v1/dms/appraisals/find-or-create", json={
        "vin": "STOLENVIN001", "mileage": 30000, "stolen_vehicle_match": True,
    }, headers=TENANT_HEADERS)).json()
    assert appraisal["status"] == "security_hold"
    assert appraisal["valuation_usd"] is None
    assert appraisal["offer_usd"] is None


async def test_stolen_vehicle_match_appears_in_compliance_holds(client):
    await client.post("/api/v1/dms/appraisals/find-or-create", json={
        "vin": "STOLENVIN002", "mileage": 30000, "stolen_vehicle_match": True,
    }, headers=TENANT_HEADERS)
    holds = (await client.get("/api/v1/dms/compliance-holds", headers=TENANT_HEADERS)).json()
    assert len(holds) == 1
    assert holds[0]["type"] == "stolen_vehicle"
    assert holds[0]["resolved"] is False


async def test_security_hold_cannot_be_valued_even_by_direct_call(client):
    """No path around the hold — even calling valuation-and-offer
    directly on a security_hold appraisal is refused."""
    appraisal = (await client.post("/api/v1/dms/appraisals/find-or-create", json={
        "vin": "STOLENVIN003", "mileage": 30000, "stolen_vehicle_match": True,
    }, headers=TENANT_HEADERS)).json()
    resp = await client.post(f"/api/v1/dms/appraisals/{appraisal['id']}/valuation-and-offer", json={"valuation_usd": 10000}, headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_accept_appraisal_creates_recon_bound_inventory_vehicle(client):
    rt = (await client.post("/api/v1/dms/rooftops", json={"name": "RT1", "oem_brand": "Toyota", "license_number": "L1", "license_expiry": "2027-01-01T00:00:00Z"}, headers=TENANT_HEADERS)).json()
    appraisal = (await client.post("/api/v1/dms/appraisals/find-or-create", json={"vin": "ACCEPTVIN001", "mileage": 25000}, headers=TENANT_HEADERS)).json()
    await client.post(f"/api/v1/dms/appraisals/{appraisal['id']}/valuation-and-offer", json={"valuation_usd": 20000}, headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/dms/appraisals/{appraisal['id']}/accept", json={"rooftop_id": rt["id"]}, headers=TENANT_HEADERS)
    assert resp.json()["status"] == "accepted"

    vehicles = (await client.get("/api/v1/dms/vehicles", headers=TENANT_HEADERS)).json()
    trade_in = next(v for v in vehicles if v["vin"] == "ACCEPTVIN001")
    assert trade_in["status"] == "in_recon"


async def test_find_or_create_reuses_pending_appraisal_for_same_vin(client):
    first = (await client.post("/api/v1/dms/appraisals/find-or-create", json={"vin": "REUSEVIN001", "mileage": 10000}, headers=TENANT_HEADERS)).json()
    second = (await client.post("/api/v1/dms/appraisals/find-or-create", json={"vin": "REUSEVIN001", "mileage": 10500}, headers=TENANT_HEADERS)).json()
    assert first["id"] == second["id"]
    assert second["mileage"] == 10500
