import uuid

from src.services import ai_orchestration_tools
from tests.conftest import TENANT_HEADERS


async def test_lifecycle_api_flow(client, monkeypatch):
    monkeypatch.setattr(
        ai_orchestration_tools, "request_winback_evaluation",
        lambda tenant_id, member_profile_id, subscription_id, reason: {"offer": "20% off for 3 months", "gu_bonus": 500},
    )

    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": "user-400", "full_name": "C", "email": "c@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]

    sub_resp = await client.post(
        "/api/v1/subscription/subscriptions",
        json={"member_profile_id": member_id, "plan_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert sub_resp.status_code == 201
    sub_id = sub_resp.json()["id"]

    initiate_resp = await client.post(
        f"/api/v1/subscription/subscriptions/{sub_id}/initiate-cancellation",
        json={"reason": "too expensive"},
        headers=TENANT_HEADERS,
    )
    assert initiate_resp.json()["winback_offer"]["offer"] == "20% off for 3 months"

    accept_resp = await client.post(f"/api/v1/subscription/subscriptions/{sub_id}/accept-winback", headers=TENANT_HEADERS)
    assert accept_resp.json()["win_back_offer_extended"] is True
    assert accept_resp.json()["status"] == "active"


async def test_finalize_cancellation_api(client):
    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": "user-401", "full_name": "D", "email": "d@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]
    sub_resp = await client.post(
        "/api/v1/subscription/subscriptions",
        json={"member_profile_id": member_id, "plan_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    sub_id = sub_resp.json()["id"]

    finalize_resp = await client.post(
        f"/api/v1/subscription/subscriptions/{sub_id}/finalize-cancellation",
        json={"reason": "moving abroad", "exit_survey": {"satisfaction": 4}},
        headers=TENANT_HEADERS,
    )
    assert finalize_resp.json()["status"] == "cancelled"


async def test_pause_resume_api(client):
    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": "user-402", "full_name": "E", "email": "e@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]
    sub_resp = await client.post(
        "/api/v1/subscription/subscriptions",
        json={"member_profile_id": member_id, "plan_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    sub_id = sub_resp.json()["id"]

    from datetime import datetime, timedelta, timezone

    pause_resp = await client.post(
        f"/api/v1/subscription/subscriptions/{sub_id}/pause",
        json={"resume_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()},
        headers=TENANT_HEADERS,
    )
    assert pause_resp.json()["status"] == "paused"

    resume_resp = await client.post(f"/api/v1/subscription/subscriptions/{sub_id}/resume", headers=TENANT_HEADERS)
    assert resume_resp.json()["status"] == "active"


async def test_finalizing_a_cancellation_announces_it_with_the_contact(client, monkeypatch):
    from src.api import lifecycle

    events = []

    async def record(topic, event_type, tenant_id, payload):
        events.append((topic, event_type, payload))

    monkeypatch.setattr(lifecycle, "publish_event", record)
    member = (await client.post("/api/v1/subscription/members", json={"user_id": str(uuid.uuid4()), "full_name": "Mia Garcia", "email": "mia@example.com"}, headers=TENANT_HEADERS)).json()
    sub = (await client.post("/api/v1/subscription/subscriptions", json={"member_profile_id": member["id"], "plan_id": str(uuid.uuid4())}, headers=TENANT_HEADERS)).json()
    resp = await client.post(f"/api/v1/subscription/subscriptions/{sub['id']}/finalize-cancellation", json={"reason": "moving"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    [(topic, event_type, payload)] = [e for e in events if e[1] == "subscription.cancelled"]
    assert topic == "subscription.lifecycle"
    assert (payload["full_name"], payload["email"], payload["reason"]) == ("Mia Garcia", "mia@example.com", "moving")
