import pytest


async def _create(client, tenant_id, order_number="SO-1001", total=15000.0):
    resp = await client.post(
        "/api/v1/sales/orders",
        json={"order_number": order_number, "customer_name": "Jane Doe", "total_amount_usd": total},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_create_order_defaults_to_draft(client, tenant_id):
    order = await _create(client, tenant_id)
    assert order["status"] == "draft"
    assert order["total_amount_usd"] == 15000.0


@pytest.mark.asyncio
async def test_list_and_get_order(client, tenant_id):
    created = await _create(client, tenant_id)

    resp = await client.get("/api/v1/sales/orders", headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1

    resp = await client.get(f"/api/v1/sales/orders/{created['id']}", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["order_number"] == "SO-1001"


@pytest.mark.asyncio
async def test_get_unknown_order_404(client, tenant_id):
    resp = await client.get(
        "/api/v1/sales/orders/00000000-0000-0000-0000-000000000000", headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_valid_transition_chain(client, tenant_id):
    order = await _create(client, tenant_id)
    order_id = order["id"]

    resp = await client.post(
        f"/api/v1/sales/orders/{order_id}/transition",
        json={"to_status": "confirmed"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"

    resp = await client.post(
        f"/api/v1/sales/orders/{order_id}/transition",
        json={"to_status": "fulfilled", "note": "delivered"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "fulfilled"


@pytest.mark.asyncio
async def test_invalid_transition_is_rejected(client, tenant_id):
    order = await _create(client, tenant_id)
    order_id = order["id"]

    resp = await client.post(
        f"/api/v1/sales/orders/{order_id}/transition",
        json={"to_status": "fulfilled"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_fulfilled_order_cannot_transition_again(client, tenant_id):
    order = await _create(client, tenant_id)
    order_id = order["id"]
    await client.post(
        f"/api/v1/sales/orders/{order_id}/transition", json={"to_status": "confirmed"}, headers={"X-Tenant-Id": tenant_id}
    )
    await client.post(
        f"/api/v1/sales/orders/{order_id}/transition", json={"to_status": "fulfilled"}, headers={"X-Tenant-Id": tenant_id}
    )
    resp = await client.post(
        f"/api/v1/sales/orders/{order_id}/transition", json={"to_status": "cancelled"}, headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_status_history_is_recorded(client, tenant_id):
    order = await _create(client, tenant_id)
    order_id = order["id"]
    await client.post(
        f"/api/v1/sales/orders/{order_id}/transition",
        json={"to_status": "confirmed", "note": "payment received"},
        headers={"X-Tenant-Id": tenant_id},
    )

    resp = await client.get(f"/api/v1/sales/orders/{order_id}/history", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) == 1
    assert history[0]["from_status"] == "draft"
    assert history[0]["to_status"] == "confirmed"
    assert history[0]["note"] == "payment received"


@pytest.mark.asyncio
async def test_requires_tenant_header(client):
    resp = await client.get("/api/v1/sales/orders")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_tenants_are_isolated(client, tenant_id):
    await _create(client, tenant_id)
    resp = await client.get("/api/v1/sales/orders", headers={"X-Tenant-Id": "some-other-tenant"})
    assert resp.json() == []
