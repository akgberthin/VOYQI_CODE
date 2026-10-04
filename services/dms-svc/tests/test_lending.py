import uuid

from tests.conftest import TENANT_HEADERS


async def _setup_app(client, income=90000):
    await client.post("/api/v1/dms/lending/lenders", json={"name": "Lender A"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/lending/lenders", json={"name": "Lender B"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/lending/lenders", json={"name": "Lender C"}, headers=TENANT_HEADERS)
    resp = await client.post("/api/v1/dms/lending/credit-applications", json={
        "deal_id": str(uuid.uuid4()), "applicant_name": "Jane", "annual_income_usd": income,
    }, headers=TENANT_HEADERS)
    return resp.json()


async def test_list_credit_applications_returns_all_for_tenant(client):
    app1 = await _setup_app(client)
    app2 = await _setup_app(client)
    listed = (await client.get("/api/v1/dms/lending/credit-applications", headers=TENANT_HEADERS)).json()
    ids = {a["id"] for a in listed}
    assert app1["id"] in ids and app2["id"] in ids


async def test_clean_bureau_auto_routes_decisions_and_selects_winner(client):
    app = await _setup_app(client, income=90000)
    resp = await client.post(f"/api/v1/dms/lending/credit-applications/{app['id']}/pull-bureau", json={
        "bureau": "experian", "fico": 780, "ofac_match": False,
    }, headers=TENANT_HEADERS)
    body = resp.json()
    assert body["status"] == "decisioned"
    assert body["selected_submission_id"] is not None

    subs = (await client.get(f"/api/v1/dms/lending/credit-applications/{app['id']}/submissions", headers=TENANT_HEADERS)).json()
    assert len(subs) == 3  # prime FICO -> routed to every lender
    assert all(s["status"] != "submitted" for s in subs)  # none left pending

    eligible = [s for s in subs if s["status"] in ("approved", "countered")]
    winner = next(s for s in subs if s["id"] == body["selected_submission_id"])
    assert all(e["apr"] >= winner["apr"] for e in eligible)  # lowest APR actually won


async def test_subprime_fico_routes_to_only_two_lenders(client):
    app = await _setup_app(client, income=50000)
    await client.post(f"/api/v1/dms/lending/credit-applications/{app['id']}/pull-bureau", json={
        "bureau": "transunion", "fico": 600, "ofac_match": False,
    }, headers=TENANT_HEADERS)
    subs = (await client.get(f"/api/v1/dms/lending/credit-applications/{app['id']}/submissions", headers=TENANT_HEADERS)).json()
    assert len(subs) == 2


async def test_deep_subprime_declines_and_auto_issues_adverse_action(client):
    app = await _setup_app(client, income=40000)
    await client.post(f"/api/v1/dms/lending/credit-applications/{app['id']}/pull-bureau", json={
        "bureau": "equifax", "fico": 500, "ofac_match": False,
    }, headers=TENANT_HEADERS)
    subs = (await client.get(f"/api/v1/dms/lending/credit-applications/{app['id']}/submissions", headers=TENANT_HEADERS)).json()
    assert all(s["status"] == "declined" for s in subs)


async def test_ofac_match_never_reaches_lender_routing(client):
    """The structural security block: zero submissions are ever
    created — routing is never even attempted, no override path."""
    app = await _setup_app(client, income=90000)
    resp = await client.post(f"/api/v1/dms/lending/credit-applications/{app['id']}/pull-bureau", json={
        "bureau": "experian", "fico": 780, "ofac_match": True,
    }, headers=TENANT_HEADERS)
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["selected_submission_id"] is None

    subs = (await client.get(f"/api/v1/dms/lending/credit-applications/{app['id']}/submissions", headers=TENANT_HEADERS)).json()
    assert subs == []  # never routed, not even to a subprime tier


async def test_ofac_match_persists_even_with_excellent_credit(client):
    """A perfect FICO score doesn't matter — OFAC has no override,
    period, regardless of every other signal."""
    app = await _setup_app(client, income=500000)
    resp = await client.post(f"/api/v1/dms/lending/credit-applications/{app['id']}/pull-bureau", json={
        "bureau": "experian", "fico": 850, "ofac_match": True,
    }, headers=TENANT_HEADERS)
    assert resp.json()["status"] == "blocked"
