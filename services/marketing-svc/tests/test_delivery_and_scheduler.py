"""Sends reach a segment's contacts through notification-svc; outcomes are
counted honestly; the scheduler runs due sends exactly once."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.services import delivery, scheduler
from tests.conftest import TENANT_HEADERS

M = "/api/v1/marketing"
H = TENANT_HEADERS


@pytest.fixture
def notif(monkeypatch):
    """notification-svc stand-in: real delivery unless told otherwise."""
    state = {"calls": [], "mode": "real_provider", "fail": set()}

    async def fake_deliver(tenant_id, channel, recipient, body, subject, source, options=None):
        state["calls"].append({"channel": channel, "recipient": recipient, "body": body, "subject": subject, "source": source, "options": options})
        if recipient in state["fail"]:
            return {"status": "failed", "delivery_mode": "real_provider", "error_message": "Twilio HTTP 400: invalid number"}
        return {"status": "sent", "delivery_mode": state["mode"], "error_message": None}

    monkeypatch.setattr(delivery, "deliver", fake_deliver)
    return state


async def _campaign_with_segment(client, channel="email", members=None, when=None):
    seg = (await client.post(f"{M}/audience/segments", json={"name": "Austin"}, headers=H)).json()
    if members is not None:
        await client.post(f"{M}/audience/segments/{seg['id']}/members", json={"members": members}, headers=H)
    camp = (await client.post(f"{M}/campaigns", json={"name": "C", "channel": channel, "audience_segment_id": seg["id"]}, headers=H)).json()
    await client.patch(f"{M}/campaigns/{camp['id']}/status", json={"status": "active"}, headers=H)
    body = "Hi {first_name}, your EV is ready." if channel != "email" else "Hi {first_name}, meet the Model Y."
    draft = (await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": channel, "body_text": body, "subject_line": "For {first_name}"}, headers=H)).json()
    at = (when or datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    send = (await client.post(f"{M}/sends", json={"campaign_id": camp["id"], "content_draft_id": draft["id"], "channel": channel, "scheduled_at": at}, headers=H)).json()
    return seg, camp, send


async def test_member_import_skips_duplicates_and_counts_members(client):
    seg = (await client.post(f"{M}/audience/segments", json={"name": "S"}, headers=H)).json()
    r = (await client.post(f"{M}/audience/segments/{seg['id']}/members", json={"members": [
        {"name": "Ava Patel", "email": "Ava@x.com"}, {"name": "Ava again", "email": "ava@x.com"}, {"phone": "+15125550101"}, {"name": "nothing"},
    ]}, headers=H)).json()
    assert r == {"added": 2, "skipped": 2}
    members = (await client.get(f"{M}/audience/segments/{seg['id']}/members", headers=H)).json()
    assert {m["email"] for m in members} == {"ava@x.com", ""}
    segs = {s["id"]: s for s in (await client.get(f"{M}/audience/segments", headers=H)).json()}
    assert segs[seg["id"]]["member_count"] == 2
    await client.delete(f"{M}/audience/segments/{seg['id']}/members/{members[0]['id']}", headers=H)
    assert {s["id"]: s for s in (await client.get(f"{M}/audience/segments", headers=H)).json()}[seg["id"]]["member_count"] == 1


async def test_send_reaches_the_segment_personalised_and_skips_suppressed(client, notif):
    _seg, camp, send = await _campaign_with_segment(client, members=[
        {"name": "Ava Patel", "email": "ava@x.com"}, {"name": "Noah Kim", "email": "noah@x.com"}, {"name": "Opted Out", "email": "out@x.com"}, {"name": "SMS only", "phone": "+1555"},
    ])
    await client.post(f"{M}/suppression", json={"channel": "email", "contact_identifier": "out@x.com", "reason": "opt_out"}, headers=H)
    done = (await client.post(f"{M}/sends/{send['id']}/execute", json={}, headers=H)).json()
    assert (done["status"], done["recipients_targeted"], done["recipients_suppressed"], done["recipients_failed"]) == ("sent", 3, 1, 0)
    assert {c["recipient"]: c["body"] for c in notif["calls"]} == {"ava@x.com": "Hi Ava, meet the Model Y.", "noah@x.com": "Hi Noah, meet the Model Y."}
    assert notif["calls"][0]["subject"] == "For Ava" and notif["calls"][0]["source"] == f"marketing:{send['id']}"
    assert (await client.get(f"{M}/analytics/campaigns/{camp['id']}/summary", headers=H)).json()["total_sent"] == 2


async def test_sms_uses_phone_numbers_and_reports_provider_failures(client, notif):
    notif["fail"].add("+15125550102")
    _seg, _camp, send = await _campaign_with_segment(client, channel="sms", members=[{"name": "A", "phone": "+15125550101"}, {"name": "B", "phone": "+15125550102"}, {"name": "C", "email": "c@x.com"}])
    done = (await client.post(f"{M}/sends/{send['id']}/execute", json={}, headers=H)).json()
    assert [c["recipient"] for c in notif["calls"]] == ["+15125550101", "+15125550102"]
    assert (done["status"], done["recipients_failed"]) == ("sent", 1) and "invalid number" in done["failure_detail"]


async def test_console_mode_is_never_reported_as_reach(client, notif):
    notif["mode"] = "console"
    _seg, camp, send = await _campaign_with_segment(client, members=[{"email": "a@x.com"}])
    done = (await client.post(f"{M}/sends/{send['id']}/execute", json={}, headers=H)).json()
    assert (done["status"], done["recipients_console"]) == ("sent", 1)
    assert (await client.get(f"{M}/analytics/campaigns/{camp['id']}/summary", headers=H)).json()["total_sent"] == 0


async def test_a_send_with_no_audience_fails_with_a_reason(client, notif):
    _seg, _camp, send = await _campaign_with_segment(client, members=[])
    done = (await client.post(f"{M}/sends/{send['id']}/execute", json={}, headers=H)).json()
    assert done["status"] == "failed" and "No recipients" in done["failure_detail"] and notif["calls"] == []


async def test_scheduler_runs_due_sends_once_and_leaves_future_ones(client, db_engine, notif):
    _s1, _c1, due = await _campaign_with_segment(client, members=[{"email": "a@x.com"}], when=datetime.now(timezone.utc) - timedelta(minutes=1))
    _s2, _c2, later = await _campaign_with_segment(client, members=[{"email": "b@x.com"}])
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    assert await scheduler.run_once(factory) == 1
    assert await scheduler.run_once(factory) == 0  # already claimed and sent
    sends = {s["id"]: s for s in (await client.get(f"{M}/sends", headers=H)).json()}
    assert sends[due["id"]]["status"] == "sent" and sends[later["id"]]["status"] == "scheduled"
    assert [c["recipient"] for c in notif["calls"]] == ["a@x.com"]
    manual = await client.post(f"{M}/sends/{due['id']}/execute", json={}, headers=H)
    assert manual.status_code == 409


async def test_scheduler_waits_for_paused_campaigns(client, db_engine, notif):
    _s, camp, due = await _campaign_with_segment(client, members=[{"email": "a@x.com"}], when=datetime.now(timezone.utc) - timedelta(minutes=1))
    await client.patch(f"{M}/campaigns/{camp['id']}/status", json={"status": "paused"}, headers=H)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    assert await scheduler.run_once(factory) == 0 and notif["calls"] == []
    await client.patch(f"{M}/campaigns/{camp['id']}/status", json={"status": "active"}, headers=H)
    assert await scheduler.run_once(factory) == 1
    assert {s["id"]: s for s in (await client.get(f"{M}/sends", headers=H)).json()}[due["id"]]["status"] == "sent"
