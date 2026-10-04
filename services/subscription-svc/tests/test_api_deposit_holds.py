import uuid

from tests.conftest import TENANT_HEADERS


async def test_place_deposit_hold_api(client):
    resp = await client.post(
        "/api/v1/subscription/deposit-holds",
        json={"member_profile_id": str(uuid.uuid4()), "mobility_class": "luxury_plus"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["amount_usd"] == 10000.0
    assert resp.json()["status"] == "placed"


async def test_place_deposit_hold_rejects_non_deposit_class(client):
    resp = await client.post(
        "/api/v1/subscription/deposit-holds",
        json={"member_profile_id": str(uuid.uuid4()), "mobility_class": "urban"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422


async def test_release_deposit_hold_api(client):
    place_resp = await client.post(
        "/api/v1/subscription/deposit-holds",
        json={"member_profile_id": str(uuid.uuid4()), "mobility_class": "performance_plus"},
        headers=TENANT_HEADERS,
    )
    hold_id = place_resp.json()["id"]

    release_resp = await client.post(f"/api/v1/subscription/deposit-holds/{hold_id}/release", headers=TENANT_HEADERS)
    assert release_resp.json()["status"] == "released"


async def test_full_capture_review_flow_api(client):
    place_resp = await client.post(
        "/api/v1/subscription/deposit-holds",
        json={"member_profile_id": str(uuid.uuid4()), "mobility_class": "luxury_plus"},
        headers=TENANT_HEADERS,
    )
    hold_id = place_resp.json()["id"]

    capture_resp = await client.post(
        f"/api/v1/subscription/deposit-holds/{hold_id}/request-capture",
        json={"amount_usd": 2000.0, "reason": "Interior damage"},
        headers=TENANT_HEADERS,
    )
    assert capture_resp.json()["status"] == "capture_pending_review"

    approve_resp = await client.post(f"/api/v1/subscription/deposit-holds/{hold_id}/approve-capture", headers=TENANT_HEADERS)
    assert approve_resp.json()["status"] == "captured"


async def test_reject_capture_flow_api(client):
    place_resp = await client.post(
        "/api/v1/subscription/deposit-holds",
        json={"member_profile_id": str(uuid.uuid4()), "mobility_class": "luxury_plus"},
        headers=TENANT_HEADERS,
    )
    hold_id = place_resp.json()["id"]
    await client.post(f"/api/v1/subscription/deposit-holds/{hold_id}/request-capture", json={"amount_usd": 500.0, "reason": "Disputed"}, headers=TENANT_HEADERS)

    reject_resp = await client.post(f"/api/v1/subscription/deposit-holds/{hold_id}/reject-capture", headers=TENANT_HEADERS)
    assert reject_resp.json()["status"] == "placed"
    assert reject_resp.json()["captured_amount_usd"] == 0.0


async def test_list_deposit_holds_filters_by_member_api(client):
    member_id = str(uuid.uuid4())
    await client.post("/api/v1/subscription/deposit-holds", json={"member_profile_id": member_id, "mobility_class": "luxury_plus"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/subscription/deposit-holds", json={"member_profile_id": str(uuid.uuid4()), "mobility_class": "performance_plus"}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/subscription/deposit-holds", params={"member_profile_id": member_id}, headers=TENANT_HEADERS)
    assert len(resp.json()) == 1
