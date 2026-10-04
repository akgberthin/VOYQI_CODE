from datetime import datetime, timedelta, timezone

from tests.conftest import TENANT_HEADERS


async def test_enrollment_api_flow(client):
    app_resp = await client.post(
        "/api/v1/subscription/applications",
        json={"applicant_name": "Marcus R.", "applicant_email": "marcus@example.com", "requested_class": "executive", "market": "miami"},
        headers=TENANT_HEADERS,
    )
    assert app_resp.status_code == 201
    app_id = app_resp.json()["id"]

    review_resp = await client.post(
        f"/api/v1/subscription/applications/{app_id}/review",
        json={"approve": True, "reviewed_by": "member-ops-1"},
        headers=TENANT_HEADERS,
    )
    assert review_resp.json()["status"] == "approved"

    enroll_resp = await client.post(
        f"/api/v1/subscription/applications/{app_id}/enroll", json={"user_id": "user-100"}, headers=TENANT_HEADERS
    )
    assert enroll_resp.status_code == 200
    member_profile_id = enroll_resp.json()["member_profile_id"]

    card_resp = await client.post(
        "/api/v1/subscription/voyqi-cards",
        json={"member_profile_id": member_profile_id, "card_id": "VQ-9001-EX"},
        headers=TENANT_HEADERS,
    )
    assert card_resp.status_code == 201
    card_pk = card_resp.json()["id"]

    bind_resp = await client.post(f"/api/v1/subscription/voyqi-cards/{card_pk}/bind", headers=TENANT_HEADERS)
    assert bind_resp.json()["is_bound"] is True


async def test_enroll_before_approval_conflict(client):
    app_resp = await client.post(
        "/api/v1/subscription/applications",
        json={"applicant_name": "X", "applicant_email": "x@example.com", "requested_class": "urban", "market": "miami"},
        headers=TENANT_HEADERS,
    )
    app_id = app_resp.json()["id"]
    enroll_resp = await client.post(
        f"/api/v1/subscription/applications/{app_id}/enroll", json={"user_id": "user-101"}, headers=TENANT_HEADERS
    )
    assert enroll_resp.status_code == 409


async def test_billing_api_flow(client):
    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": "user-200", "full_name": "A", "email": "a@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]

    start = datetime.now(timezone.utc)
    end = start + timedelta(days=30)
    invoice_resp = await client.post(
        "/api/v1/subscription/invoices",
        json={
            "member_profile_id": member_id, "subscription_id": member_id, "amount_usd": 450.0,
            "billing_period_start": start.isoformat(), "billing_period_end": end.isoformat(),
        },
        headers=TENANT_HEADERS,
    )
    assert invoice_resp.status_code == 201
    invoice_id = invoice_resp.json()["id"]

    paid_resp = await client.post(f"/api/v1/subscription/invoices/{invoice_id}/mark-paid", headers=TENANT_HEADERS)
    assert paid_resp.json()["status"] == "paid"

    list_resp = await client.get(f"/api/v1/subscription/members/{member_id}/invoices", headers=TENANT_HEADERS)
    assert len(list_resp.json()) == 1


async def test_gu_economy_api_flow(client):
    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": "user-300", "full_name": "B", "email": "b@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]

    earn_resp = await client.post(
        "/api/v1/subscription/gu/earn",
        json={"member_profile_id": member_id, "trigger": "low_mileage", "amount": 200},
        headers=TENANT_HEADERS,
    )
    assert earn_resp.status_code == 201
    assert earn_resp.json()["flagged_for_fraud"] is False

    await client.post(
        "/api/v1/subscription/gu/catalog-items",
        json={"key": "priority_selection", "name": "Priority Vehicle Selection", "gu_cost": 100},
        headers=TENANT_HEADERS,
    )
    redeem_resp = await client.post(
        "/api/v1/subscription/gu/redeem",
        json={"member_profile_id": member_id, "catalog_item_key": "priority_selection"},
        headers=TENANT_HEADERS,
    )
    assert redeem_resp.status_code == 201

    ledger_resp = await client.get(f"/api/v1/subscription/members/{member_id}/gu-ledger", headers=TENANT_HEADERS)
    assert len(ledger_resp.json()) == 2
