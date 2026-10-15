"""
Deployment-audit regression tests: the supervisor in
ai-orchestration-svc routes subscription.created and travel.activated
to the Fleet Intelligence Agent, but the publishing side was never
wired — meaning the agent would silently never fire in production.
These tests pin that both events now actually publish.
"""
import uuid

from tests.conftest import TENANT_HEADERS


async def test_create_subscription_publishes_subscription_created(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((topic, event_type, payload))

    from src.api import lifecycle
    monkeypatch.setattr(lifecycle, "publish_event", spy)

    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": "user-evt-1", "full_name": "Priya S.", "email": "priya@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]
    plan_id = str(uuid.uuid4())

    resp = await client.post(
        "/api/v1/subscription/subscriptions",
        json={"member_profile_id": member_id, "plan_id": plan_id},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201

    assert len(published) == 1
    topic, event_type, payload = published[0]
    assert event_type == "subscription.created"
    assert payload["subscription_id"] == resp.json()["id"]
    assert payload["member_profile_id"] == member_id
    assert payload["plan_id"] == plan_id


async def test_declare_travel_publishes_travel_activated(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((topic, event_type, payload))

    from src.api import continuity
    from src.services import fleet_tools
    monkeypatch.setattr(continuity, "publish_event", spy)
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda *a, **k: [])

    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": "user-evt-2", "full_name": "Marcus R.", "email": "marcus@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]
    hub_id = str(uuid.uuid4())

    resp = await client.post(
        "/api/v1/subscription/travel-declarations",
        json={
            "member_profile_id": member_id, "source_class": "executive",
            "destination_market": "Austin", "destination_hub_id": hub_id,
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201

    assert len(published) == 1
    topic, event_type, payload = published[0]
    assert event_type == "travel.activated"
    # destination_hub_id is the field the Fleet Intelligence Agent's
    # staging evaluation reads directly from the payload
    assert payload["destination_hub_id"] == hub_id
    assert payload["destination_market"] == "Austin"


async def test_subscription_and_upgrade_case_can_be_fetched_by_id(client):
    """The lookups the gateway uses to check a member owns what they act on."""
    plan = (await client.post("/api/v1/subscription/plans", json={"mobility_class": "urban", "market": "austin", "monthly_price_usd": 449}, headers=TENANT_HEADERS)).json()
    member = (await client.post("/api/v1/subscription/members", json={"user_id": str(uuid.uuid4()), "full_name": "A", "email": "a@x.com"}, headers=TENANT_HEADERS)).json()
    sub = (await client.post("/api/v1/subscription/subscriptions", json={"member_profile_id": member["id"], "plan_id": plan["id"]}, headers=TENANT_HEADERS)).json()

    fetched = await client.get(f"/api/v1/subscription/subscriptions/{sub['id']}", headers=TENANT_HEADERS)
    assert fetched.status_code == 200 and fetched.json()["member_profile_id"] == member["id"]
    assert (await client.get(f"/api/v1/subscription/subscriptions/{uuid.uuid4()}", headers=TENANT_HEADERS)).status_code == 404
    assert (await client.get(f"/api/v1/subscription/upgrade-cases/{uuid.uuid4()}", headers=TENANT_HEADERS)).status_code == 404
