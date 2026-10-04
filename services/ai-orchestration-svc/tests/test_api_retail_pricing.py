from src.services import dms_tools
from tests.conftest import TENANT_HEADERS


async def test_evaluate_retail_pricing_endpoint(client, monkeypatch):
    monkeypatch.setattr(dms_tools, "get_book_value_average", lambda tenant_id, vin: {"average_retail_value_usd": 65000.0})
    monkeypatch.setattr(dms_tools, "apply_retail_price", lambda tenant_id, vehicle_id, price_usd, reasoning: {"price_usd": price_usd})
    resp = await client.post(
        "/api/v1/ai/dms/evaluate-retail-pricing",
        json={"retail_vehicle_id": "veh-1", "vin": "WDDUG8FB0KA123456", "asking_price_usd": 68000.0, "days_in_inventory": 10, "aging_threshold_days": 60},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["recommended_price_usd"] == 65000.0
