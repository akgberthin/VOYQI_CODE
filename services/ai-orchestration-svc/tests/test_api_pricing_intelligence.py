from src.services import rental_tools
from tests.conftest import TENANT_HEADERS


async def test_evaluate_pricing_endpoint(client, monkeypatch):
    monkeypatch.setattr(rental_tools, "get_pricing_recommendation", lambda tenant_id, reservation_id: {
        "current_rate_usd": 145.0, "recommended_rate_usd": 163.0, "reasoning": "util 90% -> supply 1.13x -> demand steady 1.0x",
    })
    monkeypatch.setattr(
        rental_tools, "apply_dynamic_rate",
        lambda tenant_id, reservation_id, new_daily_rate_usd: {"id": reservation_id, "daily_rate_usd": new_daily_rate_usd},
    )
    resp = await client.post(
        "/api/v1/ai/rental/evaluate-pricing",
        json={"reservation_id": "res-1", "hub_id": "hub-1", "rental_class": "executive"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["new_rate"]["daily_rate_usd"] > 145.0
