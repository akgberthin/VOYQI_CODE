from src.services import dms_tools
from tests.conftest import TENANT_HEADERS


async def test_evaluate_max_bid_endpoint_with_book_data(client, monkeypatch):
    monkeypatch.setattr(dms_tools, "get_book_value_average", lambda tenant_id, vin: {"average_wholesale_value_usd": 42000.0})
    resp = await client.post(
        "/api/v1/ai/dms/evaluate-max-bid",
        json={"listing_id": "listing-1", "vin": "WDDUG8FB0KA777777", "condition_grade": 4.2},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["max_bid_usd"] == 38640.0


async def test_evaluate_max_bid_endpoint_refuses_without_anchor(client, monkeypatch):
    monkeypatch.setattr(dms_tools, "get_book_value_average", lambda tenant_id, vin: {"average_wholesale_value_usd": None})
    resp = await client.post(
        "/api/v1/ai/dms/evaluate-max-bid",
        json={"listing_id": "listing-2", "vin": "UNKNOWNVIN0000001"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["max_bid_usd"] is None
