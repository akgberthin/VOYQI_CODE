from datetime import datetime, timezone

import pytest

from src.services import access_session_engine


@pytest.fixture
def published(monkeypatch):
    events = []

    async def _spy(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr("src.services.access_session_engine.publish_event", _spy)
    return events


def test_off_hours_uses_business_timezone():
    # 15:00 UTC on a Wednesday is 11:00 in New York — business hours.
    assert access_session_engine.is_off_hours(datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)) is False
    # 03:00 UTC is 23:00 the previous evening in New York.
    assert access_session_engine.is_off_hours(datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)) is True
    # Saturday midday is off-hours regardless of the clock.
    assert access_session_engine.is_off_hours(datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc)) is True


async def test_start_break_glass_publishes_scoring_signals(client, tenant_id, published):
    resp = await client.post(
        "/api/v1/identity/break-glass",
        json={"user_id": "u-1", "activated_by": "u-1", "reason": "prod payments outage", "duration_minutes": 60},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "active"

    [(event_type, payload)] = published
    assert event_type == "access.break_glass_activated"
    assert payload["session_id"] == body["id"]
    assert payload["user_id"] == "u-1"
    assert payload["duration_minutes"] == 60
    assert payload["off_hours"] == body["off_hours"]


@pytest.mark.parametrize("reason", ["", "   "])
async def test_break_glass_requires_a_reason(client, tenant_id, published, reason):
    resp = await client.post(
        "/api/v1/identity/break-glass",
        json={"user_id": "u-1", "activated_by": "u-1", "reason": reason},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 422
    assert published == []


async def test_break_glass_duration_is_capped(client, tenant_id, published):
    resp = await client.post(
        "/api/v1/identity/break-glass",
        json={"user_id": "u-1", "activated_by": "u-1", "reason": "outage", "duration_minutes": 100000},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 422


async def test_end_break_glass_once_then_conflict(client, tenant_id, published):
    """The exact call identity_tools.end_break_glass_session makes when a
    human approves the agent's recommendation."""
    h = {"X-Tenant-Id": tenant_id}
    session = (
        await client.post("/api/v1/identity/break-glass", json={"user_id": "u-1", "activated_by": "u-1", "reason": "outage"}, headers=h)
    ).json()

    end = await client.post(
        f"/api/v1/identity/break-glass/{session['id']}/end",
        json={"ended_by": "ai-orchestration-svc:compliance_audit_agent", "reason": "AI-002 approved"},
        headers=h,
    )
    assert end.status_code == 200
    assert end.json()["status"] == "ended"
    assert end.json()["ended_by"] == "ai-orchestration-svc:compliance_audit_agent"

    again = await client.post(f"/api/v1/identity/break-glass/{session['id']}/end", json={"ended_by": "x"}, headers=h)
    assert again.status_code == 409

    active = (await client.get("/api/v1/identity/break-glass", params={"status": "active"}, headers=h)).json()
    assert active == []


async def test_end_unknown_session_404(client, tenant_id):
    resp = await client.post(
        "/api/v1/identity/break-glass/00000000-0000-0000-0000-000000000000/end", json={"ended_by": "x"}, headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 404


async def test_sessions_are_tenant_scoped(client, published):
    session = (
        await client.post(
            "/api/v1/identity/break-glass", json={"user_id": "u-1", "activated_by": "u-1", "reason": "outage"}, headers={"X-Tenant-Id": "tenant-a"}
        )
    ).json()
    resp = await client.post(f"/api/v1/identity/break-glass/{session['id']}/end", json={"ended_by": "x"}, headers={"X-Tenant-Id": "tenant-b"})
    assert resp.status_code == 404


async def test_impersonation_without_recording_is_flagged_in_event(client, tenant_id, published):
    h = {"X-Tenant-Id": tenant_id}
    resp = await client.post(
        "/api/v1/identity/impersonation",
        json={"admin_user_id": "admin-1", "target_user_id": "member-9", "reason": "support ticket 4411"},
        headers=h,
    )
    assert resp.status_code == 201

    [(event_type, payload)] = published
    assert event_type == "access.impersonation_started"
    assert payload["user_id"] == "admin-1"
    assert payload["target_user_id"] == "member-9"
    assert payload["has_session_recording"] is False

    end = await client.post(f"/api/v1/identity/impersonation/{resp.json()['id']}/end", json={"ended_by": "admin-1"}, headers=h)
    assert end.json()["status"] == "ended"


async def test_impersonation_with_recording(client, tenant_id, published):
    await client.post(
        "/api/v1/identity/impersonation",
        json={"admin_user_id": "admin-1", "target_user_id": "member-9", "reason": "support", "session_recording_ref": "rec-123"},
        headers={"X-Tenant-Id": tenant_id},
    )
    [(_event_type, payload)] = published
    assert payload["has_session_recording"] is True
