import uuid

import pytest

from tests.conftest import TENANT_HEADERS


@pytest.mark.asyncio
async def test_send_requires_tenant_header(client):
    resp = await client.post("/api/v1/notifications/send", json={"channel": "email", "template": "welcome_member", "recipient": "a@voyqi.com"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_send_unknown_template_returns_404(client):
    resp = await client.post("/api/v1/notifications/send", json={"channel": "email", "template": "nonexistent", "recipient": "a@voyqi.com"}, headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_seed_defaults_then_send_password_reset_matches_auth_svc_integration(client):
    """auth-svc's notification_client posts exactly this shape for
    password resets — verifying it end to end here, not just trusting
    the two services agree on paper."""
    seed_resp = await client.post("/api/v1/notifications/templates/seed-defaults", headers=TENANT_HEADERS)
    assert seed_resp.status_code == 200
    assert seed_resp.json()["templates_inserted"] >= 1

    send_resp = await client.post(
        "/api/v1/notifications/send",
        json={"channel": "email", "template": "password_reset", "recipient": "member@voyqi.com", "variables": {"reset_token": "abc123"}},
        headers=TENANT_HEADERS,
    )
    assert send_resp.status_code == 201
    body = send_resp.json()
    assert body["status"] == "sent"
    assert body["delivery_mode"] == "console"


@pytest.mark.asyncio
async def test_seed_defaults_is_idempotent(client):
    first = await client.post("/api/v1/notifications/templates/seed-defaults", headers=TENANT_HEADERS)
    second = await client.post("/api/v1/notifications/templates/seed-defaults", headers=TENANT_HEADERS)
    assert first.json()["templates_inserted"] >= 1
    assert second.json()["templates_inserted"] == 0


@pytest.mark.asyncio
async def test_list_notifications_reflects_sent_history(client):
    await client.post("/api/v1/notifications/templates/seed-defaults", headers=TENANT_HEADERS)
    await client.post(
        "/api/v1/notifications/send",
        json={"channel": "email", "template": "welcome_member", "recipient": "member2@voyqi.com", "variables": {"member_name": "Garth", "plan_class": "Executive"}},
        headers=TENANT_HEADERS,
    )
    list_resp = await client.get("/api/v1/notifications/notifications", params={"recipient": "member2@voyqi.com"}, headers=TENANT_HEADERS)
    assert list_resp.status_code == 200
    rows = list_resp.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "sent"


@pytest.mark.asyncio
async def test_preference_put_then_notification_is_skipped(client):
    await client.post("/api/v1/notifications/templates/seed-defaults", headers=TENANT_HEADERS)
    user_id = str(uuid.uuid4())

    pref_resp = await client.put(
        "/api/v1/notifications/preferences",
        json={"user_id": user_id, "category": "marketing", "channel": "email", "opted_in": False},
        headers=TENANT_HEADERS,
    )
    assert pref_resp.status_code == 200

    send_resp = await client.post(
        "/api/v1/notifications/send",
        json={"channel": "email", "template": "upgrade_case_offered", "recipient": "member3@voyqi.com", "user_id": user_id, "variables": {"from_class": "Executive", "to_class": "Luxury"}},
        headers=TENANT_HEADERS,
    )
    assert send_resp.status_code == 201
    assert send_resp.json()["status"] == "skipped_opted_out"


@pytest.mark.asyncio
async def test_cannot_opt_out_of_security_category_via_api(client):
    resp = await client.put(
        "/api/v1/notifications/preferences",
        json={"user_id": str(uuid.uuid4()), "category": "security", "channel": "email", "opted_in": False},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 400


async def test_send_publishes_delivery_outcome_without_recipient(client, monkeypatch):
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append((event_type, payload))

    monkeypatch.setattr("src.api.notifications.publish_event", _spy)
    await client.post("/api/v1/notifications/templates/seed-defaults", headers=TENANT_HEADERS)
    resp = await client.post(
        "/api/v1/notifications/send", json={"channel": "email", "template": "welcome_member", "recipient": "secret@voyqi.com", "variables": {"member_name": "Ada", "plan_class": "Urban"}}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 201

    [(event_type, payload)] = events
    assert event_type == "notification." + resp.json()["status"]
    assert payload["notification_id"] == str(resp.json()["id"])
    assert "secret@voyqi.com" not in repr(payload)
