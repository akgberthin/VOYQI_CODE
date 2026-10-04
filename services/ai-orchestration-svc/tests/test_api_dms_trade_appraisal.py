from tests.conftest import TENANT_HEADERS


async def test_evaluate_trade_appraisal_endpoint_deterministic_fallback(client):
    resp = await client.post(
        "/api/v1/ai/dms/evaluate-trade-appraisal",
        json={"appraisal_id": "appraisal-1", "vin": "1HGCM82633A004352", "mileage": 45000, "condition_notes": "minor wear"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["valuation_usd"] is None
    assert "cannot be produced deterministically" in resp.json()["reasoning"]
