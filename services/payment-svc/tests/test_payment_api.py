import uuid

import pytest

from tests.conftest import TENANT_HEADERS


async def _add_method(client, user_id=None, last4="4242"):
    resp = await client.post(
        "/api/v1/payment/payment-methods",
        json={"user_id": str(user_id or uuid.uuid4()), "provider_token": "tok_test", "method_type": "card", "last4": last4, "brand": "visa", "exp_month": 12, "exp_year": 2030},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_add_payment_method(client):
    body = await _add_method(client)
    assert body["last4"] == "4242"
    assert body["provider_mode"] == "sandbox"


@pytest.mark.asyncio
async def test_charge_requires_idempotency_key_header(client):
    method = await _add_method(client)
    user_id = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/payment/charges",
        json={"user_id": user_id, "payment_method_id": method["id"], "amount_cents": 5000},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 400
    assert "Idempotency-Key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_charge_succeeds_with_idempotency_key(client):
    method = await _add_method(client)
    resp = await client.post(
        "/api/v1/payment/charges",
        json={"user_id": str(uuid.uuid4()), "payment_method_id": method["id"], "amount_cents": 5000},
        headers={**TENANT_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_retrying_the_same_idempotency_key_returns_the_same_charge_via_api():
    """The end-to-end version of the core guarantee: a client retrying
    an HTTP request (e.g. after a timeout) with the same Idempotency-Key
    must get back the same charge, not a second one."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from sqlalchemy.pool import StaticPool

    from src.core.database import Base, get_db
    from src.main import app

    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        method = await _add_method(client)
        key = str(uuid.uuid4())
        payload = {"user_id": str(uuid.uuid4()), "payment_method_id": method["id"], "amount_cents": 5000}

        first = await client.post("/api/v1/payment/charges", json=payload, headers={**TENANT_HEADERS, "Idempotency-Key": key})
        second = await client.post("/api/v1/payment/charges", json=payload, headers={**TENANT_HEADERS, "Idempotency-Key": key})

    app.dependency_overrides.clear()
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_charge_unknown_payment_method_returns_404(client):
    resp = await client.post(
        "/api/v1/payment/charges",
        json={"user_id": str(uuid.uuid4()), "payment_method_id": str(uuid.uuid4()), "amount_cents": 5000},
        headers={**TENANT_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_charge_returns_it(client):
    method = await _add_method(client)
    create_resp = await client.post(
        "/api/v1/payment/charges",
        json={"user_id": str(uuid.uuid4()), "payment_method_id": method["id"], "amount_cents": 5000},
        headers={**TENANT_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    charge_id = create_resp.json()["id"]

    get_resp = await client.get(f"/api/v1/payment/charges/{charge_id}", headers=TENANT_HEADERS)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == charge_id


@pytest.mark.asyncio
async def test_refund_flow_via_api(client):
    method = await _add_method(client)
    create_resp = await client.post(
        "/api/v1/payment/charges",
        json={"user_id": str(uuid.uuid4()), "payment_method_id": method["id"], "amount_cents": 5000},
        headers={**TENANT_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    charge_id = create_resp.json()["id"]

    refund_resp = await client.post(f"/api/v1/payment/charges/{charge_id}/refund", json={}, headers=TENANT_HEADERS)
    assert refund_resp.status_code == 201
    assert refund_resp.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_refund_exceeding_remaining_amount_returns_422(client):
    method = await _add_method(client)
    create_resp = await client.post(
        "/api/v1/payment/charges",
        json={"user_id": str(uuid.uuid4()), "payment_method_id": method["id"], "amount_cents": 5000},
        headers={**TENANT_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    charge_id = create_resp.json()["id"]

    resp = await client.post(f"/api/v1/payment/charges/{charge_id}/refund", json={"amount_cents": 999999}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_webhook_idempotent_via_api(client):
    payload = {"provider": "stripe", "provider_event_id": "evt_api_test", "event_type": "charge.succeeded", "payload": {"id": "ch_1"}}
    first = await client.post("/api/v1/payment/webhooks", json=payload, headers=TENANT_HEADERS)
    second = await client.post("/api/v1/payment/webhooks", json=payload, headers=TENANT_HEADERS)
    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "already_processed"


@pytest.mark.asyncio
async def test_payment_events_published_once_per_real_state_change(client, monkeypatch):
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append(event_type)

    monkeypatch.setattr("src.api.payments.publish_event", _spy)
    method = await _add_method(client)
    key = {**TENANT_HEADERS, "Idempotency-Key": str(uuid.uuid4())}
    body = {"user_id": str(uuid.uuid4()), "payment_method_id": method["id"], "amount_cents": 5000}

    charge = (await client.post("/api/v1/payment/charges", json=body, headers=key)).json()
    await client.post("/api/v1/payment/charges", json=body, headers=key)  # idempotent retry
    await client.post(f"/api/v1/payment/charges/{charge['id']}/refund", json={"amount_cents": 1000, "reason": "goodwill"}, headers=TENANT_HEADERS)

    assert events == ["payment.method_added", "payment.processed", "payment.refunded"]
