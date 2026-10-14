import uuid

import pytest

from tests.conftest import TENANT_HEADERS


@pytest.mark.asyncio
async def test_set_and_get_current_price(client):
    set_resp = await client.post(
        "/api/v1/pricing/rate-cards",
        json={"mobility_class": "executive", "market": "national", "price_cents": 85900, "changed_by": "admin"},
        headers=TENANT_HEADERS,
    )
    assert set_resp.status_code == 201

    get_resp = await client.get("/api/v1/pricing/rate-cards/current", params={"mobility_class": "executive"}, headers=TENANT_HEADERS)
    assert get_resp.status_code == 200
    assert get_resp.json()["price_cents"] == 85900


@pytest.mark.asyncio
async def test_get_current_price_404_when_no_rate_card(client):
    resp = await client.get("/api/v1/pricing/rate-cards/current", params={"mobility_class": "does_not_exist"}, headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_price_history_via_api(client):
    await client.post("/api/v1/pricing/rate-cards", json={"mobility_class": "urban", "price_cents": 44900, "changed_by": "admin"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/pricing/rate-cards", json={"mobility_class": "urban", "price_cents": 46900, "changed_by": "admin", "reason": "increase"}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/pricing/rate-cards/history", params={"mobility_class": "urban"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_calculate_endpoint_with_promo(client):
    await client.post("/api/v1/pricing/rate-cards", json={"mobility_class": "luxury", "price_cents": 100000, "changed_by": "admin"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/pricing/promo-codes", json={"code": "API10", "discount_type": "percentage", "discount_value": 10}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/pricing/calculate", params={"mobility_class": "luxury", "promo_code": "API10"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["final_price_cents"] == 90000


@pytest.mark.asyncio
async def test_redeem_promo_via_api_and_limit_enforced(client):
    await client.post("/api/v1/pricing/rate-cards", json={"mobility_class": "urban", "price_cents": 44900, "changed_by": "admin"}, headers=TENANT_HEADERS)
    await client.post(
        "/api/v1/pricing/promo-codes",
        json={"code": "APIONCE", "discount_type": "percentage", "discount_value": 10, "max_redemptions_per_user": 1},
        headers=TENANT_HEADERS,
    )
    user_id = str(uuid.uuid4())

    first = await client.post(
        "/api/v1/pricing/promo-codes/redeem",
        json={"code": "APIONCE", "user_id": user_id, "mobility_class": "urban", "order_reference": "o1"},
        headers=TENANT_HEADERS,
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/pricing/promo-codes/redeem",
        json={"code": "APIONCE", "user_id": user_id, "mobility_class": "urban", "order_reference": "o2"},
        headers=TENANT_HEADERS,
    )
    assert second.status_code == 422


@pytest.mark.asyncio
async def test_set_price_rejects_non_positive_via_api(client):
    resp = await client.post("/api/v1/pricing/rate-cards", json={"mobility_class": "urban", "price_cents": -100, "changed_by": "admin"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pricing_events(client, monkeypatch):
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append(event_type)

    monkeypatch.setattr("src.api.pricing.publish_event", _spy)
    await client.post("/api/v1/pricing/rate-cards", json={"mobility_class": "urban", "price_cents": 44900, "changed_by": "admin"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/pricing/promo-codes", json={"code": "EVT10", "discount_type": "percentage", "discount_value": 10, "max_redemptions_per_user": 1}, headers=TENANT_HEADERS)
    body = {"code": "EVT10", "user_id": str(uuid.uuid4()), "mobility_class": "urban"}
    await client.post("/api/v1/pricing/promo-codes/redeem", json=body, headers=TENANT_HEADERS)
    rejected = await client.post("/api/v1/pricing/promo-codes/redeem", json=body, headers=TENANT_HEADERS)

    assert rejected.status_code == 422
    assert events == ["pricing.price_set", "pricing.promo_created", "pricing.promo_redeemed"]
