from tests.conftest import TENANT_HEADERS


async def _rooftop(client, name="RT"):
    resp = await client.post("/api/v1/dms/rooftops", json={"name": name, "oem_brand": "Toyota", "license_number": f"L-{name}", "license_expiry": "2027-01-01T00:00:00Z"}, headers=TENANT_HEADERS)
    return resp.json()["id"]


async def test_list_pl_snapshots_returns_all_for_tenant(client):
    rt = await _rooftop(client, "ListTest")
    await client.post("/api/v1/dms/performance/pl-snapshots", json={
        "rooftop_id": rt, "units": 8, "gross_usd": 12000, "turn_rate": 1.2, "fi_pvr_usd": 1200, "absorption_pct": 70,
    }, headers=TENANT_HEADERS)
    listed = (await client.get("/api/v1/dms/performance/pl-snapshots", headers=TENANT_HEADERS)).json()
    assert any(s["rooftop_id"] == rt for s in listed)


async def test_strong_rooftop_gets_no_intervention(client):
    rt = await _rooftop(client, "Strong")
    await client.post("/api/v1/dms/performance/pl-snapshots", json={
        "rooftop_id": rt, "units": 10, "gross_usd": 15000, "turn_rate": 1.5, "fi_pvr_usd": 1500, "absorption_pct": 80,
    }, headers=TENANT_HEADERS)
    evals = (await client.post("/api/v1/dms/performance/evaluate", headers=TENANT_HEADERS)).json()
    assert evals[0]["verdict"] == "strong"
    assert evals[0]["points"] == 0


async def test_intervene_verdict_triggers_accelerated_markdown(client):
    rt = await _rooftop(client, "Weak")
    await client.post("/api/v1/dms/franchise", json={"rooftop_id": rt, "status": "non_compliant", "score": 61}, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/performance/pl-snapshots", json={
        "rooftop_id": rt, "units": 11, "gross_usd": 4100, "turn_rate": 1.4, "fi_pvr_usd": 980, "absorption_pct": 48,
    }, headers=TENANT_HEADERS)
    # A second, stronger rooftop so this one's gross is genuinely below the group average.
    rt2 = await _rooftop(client, "Strong2")
    await client.post("/api/v1/dms/performance/pl-snapshots", json={
        "rooftop_id": rt2, "units": 6, "gross_usd": 12000, "turn_rate": 1.5, "fi_pvr_usd": 1600, "absorption_pct": 75,
    }, headers=TENANT_HEADERS)

    v = (await client.post("/api/v1/dms/vehicles", json={
        "rooftop_id": rt, "vin": "PLVIN001", "description": "Aging unit", "condition": "used",
        "price_usd": 20000, "cost_usd": 17000, "days_in_inventory": 45,
    }, headers=TENANT_HEADERS)).json()

    evals = (await client.post("/api/v1/dms/performance/evaluate-and-intervene", headers=TENANT_HEADERS)).json()
    weak_eval = next(e for e in evals if e["rooftop_id"] == rt)
    assert weak_eval["verdict"] == "intervene"
    assert weak_eval["points"] >= 3
    assert weak_eval["intervention_applied"] is True

    vehicle_after = (await client.get(f"/api/v1/dms/vehicles/{v['id']}", headers=TENANT_HEADERS)).json()
    assert vehicle_after["price_usd"] < 20000  # accelerated markdown actually applied, not just labeled


async def test_intervention_markdown_does_not_compound_across_repeated_calls(client):
    """Regression test: apply_accelerated_markdown must guard against
    the same vehicle being re-discounted every time the capstone
    endpoint is called, exactly like the standard aging sweep does."""
    rt = await _rooftop(client, "Weak3")
    rt2 = await _rooftop(client, "Strong4")
    await client.post("/api/v1/dms/franchise", json={"rooftop_id": rt, "status": "non_compliant", "score": 55}, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/performance/pl-snapshots", json={
        "rooftop_id": rt, "units": 11, "gross_usd": 4100, "turn_rate": 1.4, "fi_pvr_usd": 980, "absorption_pct": 40,
    }, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/performance/pl-snapshots", json={
        "rooftop_id": rt2, "units": 6, "gross_usd": 12000, "turn_rate": 1.5, "fi_pvr_usd": 1600, "absorption_pct": 75,
    }, headers=TENANT_HEADERS)
    v = (await client.post("/api/v1/dms/vehicles", json={
        "rooftop_id": rt, "vin": "COMPOUNDTEST01", "description": "x", "condition": "used",
        "price_usd": 20000, "cost_usd": 17000, "days_in_inventory": 45,
    }, headers=TENANT_HEADERS)).json()

    await client.post("/api/v1/dms/performance/evaluate-and-intervene", headers=TENANT_HEADERS)
    price_after_first = (await client.get(f"/api/v1/dms/vehicles/{v['id']}", headers=TENANT_HEADERS)).json()["price_usd"]
    await client.post("/api/v1/dms/performance/evaluate-and-intervene", headers=TENANT_HEADERS)
    price_after_second = (await client.get(f"/api/v1/dms/vehicles/{v['id']}", headers=TENANT_HEADERS)).json()["price_usd"]
    assert price_after_second == price_after_first


async def test_intervention_does_not_reapply_on_second_evaluation(client):
    rt = await _rooftop(client, "Weak2")
    await client.post("/api/v1/dms/franchise", json={"rooftop_id": rt, "status": "non_compliant", "score": 55}, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/performance/pl-snapshots", json={
        "rooftop_id": rt, "units": 11, "gross_usd": 4100, "turn_rate": 1.4, "fi_pvr_usd": 980, "absorption_pct": 40,
    }, headers=TENANT_HEADERS)
    rt2 = await _rooftop(client, "Strong3")
    await client.post("/api/v1/dms/performance/pl-snapshots", json={
        "rooftop_id": rt2, "units": 6, "gross_usd": 12000, "turn_rate": 1.5, "fi_pvr_usd": 1600, "absorption_pct": 75,
    }, headers=TENANT_HEADERS)

    first = (await client.post("/api/v1/dms/performance/evaluate-and-intervene", headers=TENANT_HEADERS)).json()
    weak_first = next(e for e in first if e["rooftop_id"] == rt)
    assert weak_first["verdict"] == "intervene"
    assert weak_first["intervention_applied"] is True
    second = (await client.post("/api/v1/dms/performance/evaluate-and-intervene", headers=TENANT_HEADERS)).json()
    weak_second = next(e for e in second if e["rooftop_id"] == rt)
    # A fresh PerformanceEvaluation row is written each call (audit
    # trail), but the SAME rooftop's vehicles are never re-swept twice.
    assert weak_second["intervention_applied"] is True
