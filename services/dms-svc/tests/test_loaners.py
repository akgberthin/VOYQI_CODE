from tests.conftest import TENANT_HEADERS


async def test_service_dispatch_creates_out_loaner(client):
    rt = (await client.post("/api/v1/dms/rooftops", json={"name": "RT1", "oem_brand": "Toyota", "license_number": "L1", "license_expiry": "2027-01-01T00:00:00Z"}, headers=TENANT_HEADERS)).json()
    await client.post("/api/v1/dms/service/repair-orders", json={
        "rooftop_id": rt["id"], "vin": "LOANVIN001", "customer_name": "Overdue Test", "description": "engine repair", "warranty": False,
    }, headers=TENANT_HEADERS)
    loaners = (await client.get("/api/v1/dms/loaners", headers=TENANT_HEADERS)).json()
    loaner = next(l for l in loaners if l["customer_name"] == "Overdue Test")
    assert loaner["status"] == "out"
    assert loaner["billing_flag"] is False


async def test_overdue_loaner_auto_flagged_for_billing(client, db_session):
    from src.services import loaner_engine, service_engine

    rt_resp = await client.post("/api/v1/dms/rooftops", json={"name": "RT1", "oem_brand": "Toyota", "license_number": "L1", "license_expiry": "2027-01-01T00:00:00Z"}, headers=TENANT_HEADERS)
    rt = rt_resp.json()
    await client.post("/api/v1/dms/service/repair-orders", json={
        "rooftop_id": rt["id"], "vin": "LOANVIN002", "customer_name": "Long Term Customer", "description": "engine repair", "warranty": False,
    }, headers=TENANT_HEADERS)

    # days_out ticks up over real time in production; directly set it
    # here the same way fleet-svc's tests manipulate aging fields with
    # no dedicated increment endpoint.
    loaners = await loaner_engine.list_loaners(db_session, "voyqi-holdings")
    loaner = next(l for l in loaners if l.customer_name == "Long Term Customer")
    loaner.days_out = 6
    await db_session.commit()

    flagged = await loaner_engine.evaluate_overdue_loaners(db_session, "voyqi-holdings")
    assert len(flagged) == 1
    assert flagged[0].billing_flag is True


async def test_return_loaner_sets_returned_status(client):
    rt = (await client.post("/api/v1/dms/rooftops", json={"name": "RT1", "oem_brand": "Toyota", "license_number": "L1", "license_expiry": "2027-01-01T00:00:00Z"}, headers=TENANT_HEADERS)).json()
    await client.post("/api/v1/dms/service/repair-orders", json={
        "rooftop_id": rt["id"], "vin": "LOANVIN003", "customer_name": "Return Test", "description": "brake job", "warranty": False,
    }, headers=TENANT_HEADERS)
    loaners = (await client.get("/api/v1/dms/loaners", headers=TENANT_HEADERS)).json()
    loaner = next(l for l in loaners if l["customer_name"] == "Return Test")
    resp = await client.post(f"/api/v1/dms/loaners/{loaner['id']}/return", headers=TENANT_HEADERS)
    assert resp.json()["status"] == "returned"
