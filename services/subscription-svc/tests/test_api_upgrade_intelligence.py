import uuid

from src.services import upgrade_intelligence_engine
from tests.conftest import TENANT_HEADERS


async def _create_member_and_subscription(client, mobility_class: str, monthly_price: float):
    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": f"user-{uuid.uuid4()}", "full_name": "Upgrade Test", "email": f"{uuid.uuid4()}@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]

    plan_resp = await client.post(
        "/api/v1/subscription/plans",
        json={"mobility_class": mobility_class, "market": "miami", "monthly_price_usd": monthly_price, "entitlements": {}},
        headers=TENANT_HEADERS,
    )
    plan_id = plan_resp.json()["id"]

    sub_resp = await client.post(
        "/api/v1/subscription/subscriptions",
        json={"member_profile_id": member_id, "plan_id": plan_id},
        headers=TENANT_HEADERS,
    )
    return member_id, sub_resp.json()["id"], plan_id


async def test_recording_above_class_requests_via_api(client):
    member_id, subscription_id, _ = await _create_member_and_subscription(client, "executive", 859.0)
    resp = await client.post(
        "/api/v1/subscription/above-class-requests",
        json={"member_profile_id": member_id, "subscription_id": subscription_id, "subscribed_class": "executive", "requested_class": "luxury"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 202


async def test_full_upgrade_flow_offer_to_accept_via_api(client, db_session):
    """End to end through the real HTTP layer for everything that has
    a public endpoint. Case creation has no public endpoint by
    design — only the agent creates cases — so it's seeded directly
    through the engine, sharing the same underlying database as the
    HTTP client via the shared db_engine fixture."""
    member_id, subscription_id, _executive_plan_id = await _create_member_and_subscription(client, "executive", 859.0)
    await client.post(
        "/api/v1/subscription/plans",
        json={"mobility_class": "luxury", "market": "miami", "monthly_price_usd": 1499.0, "entitlements": {}},
        headers=TENANT_HEADERS,
    )

    from src.models import MobilityClass
    case = await upgrade_intelligence_engine.create_upgrade_case(
        db_session, "voyqi-holdings", uuid.UUID(member_id), uuid.UUID(subscription_id),
        MobilityClass.EXECUTIVE, MobilityClass.LUXURY, 3, "3 luxury-class requests in 30 days",
    )

    list_resp = await client.get("/api/v1/subscription/upgrade-cases", params={"member_profile_id": member_id}, headers=TENANT_HEADERS)
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["status"] == "offered"

    accept_resp = await client.post(f"/api/v1/subscription/upgrade-cases/{case.id}/accept", headers=TENANT_HEADERS)
    assert accept_resp.status_code == 200
    assert accept_resp.json()["status"] == "accepted"

    from sqlalchemy import select

    from src.models import Subscription
    result = await db_session.execute(select(Subscription).where(Subscription.id == uuid.UUID(subscription_id)))
    refreshed_subscription = result.scalar_one()
    assert str(refreshed_subscription.plan_id) == accept_resp.json()["new_plan_id"]


async def test_declining_an_upgrade_case_via_api(client, db_session):
    member_id, subscription_id, executive_plan_id = await _create_member_and_subscription(client, "executive", 859.0)

    from src.models import MobilityClass
    case = await upgrade_intelligence_engine.create_upgrade_case(
        db_session, "voyqi-holdings", uuid.UUID(member_id), uuid.UUID(subscription_id),
        MobilityClass.EXECUTIVE, MobilityClass.LUXURY, 3, "pattern met",
    )

    decline_resp = await client.post(f"/api/v1/subscription/upgrade-cases/{case.id}/decline", headers=TENANT_HEADERS)
    assert decline_resp.json()["status"] == "declined"

    from sqlalchemy import select

    from src.models import Subscription
    result = await db_session.execute(select(Subscription).where(Subscription.id == uuid.UUID(subscription_id)))
    refreshed_subscription = result.scalar_one()
    assert str(refreshed_subscription.plan_id) == executive_plan_id  # unchanged


async def test_accepting_already_declined_case_returns_409(client, db_session):
    member_id, subscription_id, _ = await _create_member_and_subscription(client, "executive", 859.0)

    from src.models import MobilityClass
    case = await upgrade_intelligence_engine.create_upgrade_case(
        db_session, "voyqi-holdings", uuid.UUID(member_id), uuid.UUID(subscription_id),
        MobilityClass.EXECUTIVE, MobilityClass.LUXURY, 3, "pattern met",
    )
    await client.post(f"/api/v1/subscription/upgrade-cases/{case.id}/decline", headers=TENANT_HEADERS)

    accept_resp = await client.post(f"/api/v1/subscription/upgrade-cases/{case.id}/accept", headers=TENANT_HEADERS)
    assert accept_resp.status_code == 409


async def test_request_upgrade_evaluation_endpoint(client):
    resp = await client.post(f"/api/v1/subscription/members/{uuid.uuid4()}/request-upgrade-evaluation", headers=TENANT_HEADERS)
    assert resp.status_code == 202


async def test_upgrade_pattern_endpoint_below_threshold(client):
    member_id, _, _ = await _create_member_and_subscription(client, "executive", 859.0)
    resp = await client.get(f"/api/v1/subscription/members/{member_id}/upgrade-pattern", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["pattern_detected"] is False


async def test_upgrade_pattern_endpoint_detects_real_pattern(client):
    member_id, subscription_id, _ = await _create_member_and_subscription(client, "executive", 859.0)
    for _ in range(3):
        await client.post(
            "/api/v1/subscription/above-class-requests",
            json={"member_profile_id": member_id, "subscription_id": subscription_id, "subscribed_class": "executive", "requested_class": "luxury"},
            headers=TENANT_HEADERS,
        )
    resp = await client.get(f"/api/v1/subscription/members/{member_id}/upgrade-pattern", headers=TENANT_HEADERS)
    assert resp.json()["pattern_detected"] is True
    assert resp.json()["recommended_class"] == "luxury"


async def test_create_upgrade_case_via_api(client):
    member_id, subscription_id, _ = await _create_member_and_subscription(client, "executive", 859.0)
    resp = await client.post(
        "/api/v1/subscription/upgrade-cases",
        json={"member_profile_id": member_id, "subscription_id": subscription_id, "current_class": "executive", "recommended_class": "luxury", "above_class_request_count": 3, "reasoning": "pattern met"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "offered"
