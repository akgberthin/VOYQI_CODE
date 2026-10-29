"""Phase 4: automation journeys, voice compliance (consent, calling window),
recurring sends and posts, and the marketing report."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core import event_consumer
from src.models import JourneyEnrollment
from src.services import channel_policy, delivery, scheduler
from tests.conftest import TENANT_HEADERS

M = "/api/v1/marketing"
H = TENANT_HEADERS
TENANT = H["X-Tenant-Id"]
WELCOME = [
    {"delay_hours": 0, "channel": "email", "subject": "Welcome {first_name}", "body": "Hi {first_name}, welcome."},
    {"delay_hours": 24, "channel": "sms", "body": "VOYQI: first trip, {first_name}? Reply STOP to opt out."},
    {"delay_hours": 72, "channel": "voice", "body": "Hello {first_name}, this is VOYQI."},
]


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake(tenant_id, channel, recipient, body, subject, source, options=None):
        calls.append({"channel": channel, "recipient": recipient, "body": body, "subject": subject, "source": source})
        return {"status": "sent", "delivery_mode": "real_provider", "error_message": None}

    monkeypatch.setattr(delivery, "deliver", fake)
    return calls


@pytest.fixture
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
def window(monkeypatch):
    state = {"open": True}
    monkeypatch.setattr(channel_policy, "in_voice_window", lambda settings, now=None: state["open"])
    return state


async def _journey(client, trigger="user.created", steps=WELCOME, active=True):
    j = (await client.post(f"{M}/journeys", json={"name": "Welcome", "trigger_event": trigger, "steps": steps}, headers=H)).json()
    if active:
        await client.patch(f"{M}/journeys/{j['id']}", json={"status": "active"}, headers=H)
    return j


async def _advance(db_engine, journey_id):
    """Make every active enrollment's next step due now."""
    async with async_sessionmaker(db_engine, class_=AsyncSession)() as db:
        await db.execute(update(JourneyEnrollment).where(JourneyEnrollment.journey_id == journey_id, JourneyEnrollment.status == "active")
                         .values(next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()


async def test_templates_and_validation(client):
    templates = (await client.get(f"{M}/journeys/templates", headers=H)).json()
    assert {t["key"] for t in templates} == {"welcome", "lead_nurture", "winback", "churn_prevention"}
    bad = await client.post(f"{M}/journeys", json={"name": "x", "steps": [{"channel": "fax", "body": "x"}]}, headers=H)
    assert bad.status_code == 422 and "channel" in bad.json()["detail"]
    assert (await client.post(f"{M}/journeys", json={"name": "x", "steps": []}, headers=H)).status_code == 422


async def test_an_event_enrols_the_contact_once_and_only_in_active_journeys(client, factory):
    active = await _journey(client)
    paused = await _journey(client, active=False)
    raw = json.dumps({"event_type": "user.created", "tenant_id": TENANT, "payload": {"user_id": "u1", "email": "Ava@x.com"}}).encode()
    assert await event_consumer.handle(factory, raw) == 1
    assert await event_consumer.handle(factory, raw) == 0  # already enrolled
    assert await event_consumer.handle(factory, json.dumps({"event_type": "user.created", "tenant_id": TENANT, "payload": {"user_id": "u2"}}).encode()) == 0
    assert await event_consumer.handle(factory, b"not json") == 0
    assert len((await client.get(f"{M}/journeys/{active['id']}/enrollments", headers=H)).json()) == 1
    assert (await client.get(f"{M}/journeys/{paused['id']}/enrollments", headers=H)).json() == []
    listed = {j["id"]: j for j in (await client.get(f"{M}/journeys", headers=H)).json()}
    assert listed[active["id"]]["enrollments"] == {"active": 1}


async def test_steps_go_out_in_order_with_their_delays(client, db_engine, factory, sent, window):
    j = await _journey(client)
    await client.post(f"{M}/journeys/{j['id']}/enroll", json={"contacts": [{"name": "Ava Patel", "email": "ava@x.com", "phone": "+1555", "consent_voice": True}]}, headers=H)
    assert await scheduler.run_once(factory) == 1
    assert sent[-1] == {"channel": "email", "recipient": "ava@x.com", "body": "Hi Ava, welcome.", "subject": "Welcome Ava", "source": f"journey:{j['id']}"}
    [e] = (await client.get(f"{M}/journeys/{j['id']}/enrollments", headers=H)).json()
    due_in = datetime.fromisoformat(e["next_run_at"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
    assert e["current_step"] == 1 and timedelta(hours=23) < due_in <= timedelta(hours=24)
    assert await scheduler.run_once(factory) == 0  # not due yet
    await _advance(db_engine, j["id"])
    await scheduler.run_once(factory)
    await _advance(db_engine, j["id"])
    await scheduler.run_once(factory)
    assert [c["channel"] for c in sent] == ["email", "sms", "voice"]
    [e] = (await client.get(f"{M}/journeys/{j['id']}/enrollments", headers=H)).json()
    assert e["status"] == "completed" and [h["outcome"] for h in e["history"]] == ["sent (real_provider)"] * 3


async def test_journeys_respect_suppression_voice_consent_and_the_calling_window(client, db_engine, factory, sent, window):
    await client.post(f"{M}/suppression", json={"channel": "email", "contact_identifier": "out@x.com", "reason": "opt_out"}, headers=H)
    j = await _journey(client, steps=[WELCOME[0], {"delay_hours": 1, "channel": "voice", "body": "Hi"}])
    await client.post(f"{M}/journeys/{j['id']}/enroll", json={"contacts": [{"email": "out@x.com", "phone": "+1555"}]}, headers=H)
    await scheduler.run_once(factory)
    await _advance(db_engine, j["id"])
    window["open"] = False
    await scheduler.run_once(factory)
    [e] = (await client.get(f"{M}/journeys/{j['id']}/enrollments", headers=H)).json()
    assert e["history"][0]["outcome"] == "skipped: suppressed" and e["current_step"] == 1  # voice waiting for the window
    window["open"] = True
    await _advance(db_engine, j["id"])
    await scheduler.run_once(factory)
    [e] = (await client.get(f"{M}/journeys/{j['id']}/enrollments", headers=H)).json()
    assert e["history"][1]["outcome"] == "skipped: no voice consent" and sent == []


async def test_segment_enrolment_and_stopping(client):
    j = await _journey(client, trigger="manual")
    seg = (await client.post(f"{M}/audience/segments", json={"name": "S"}, headers=H)).json()
    await client.post(f"{M}/audience/segments/{seg['id']}/members", json={"members": [{"email": "a@x.com"}, {"email": "b@x.com"}]}, headers=H)
    assert (await client.post(f"{M}/journeys/{j['id']}/enroll", json={"segment_id": seg["id"]}, headers=H)).json() == {"enrolled": 2}
    assert (await client.post(f"{M}/journeys/{j['id']}/enroll", json={"segment_id": seg["id"]}, headers=H)).json() == {"enrolled": 0}
    first = (await client.get(f"{M}/journeys/{j['id']}/enrollments", headers=H)).json()[0]
    assert (await client.post(f"{M}/journeys/enrollments/{first['id']}/stop", headers=H)).json()["status"] == "stopped"


async def _voice_send(client, members=None, when=None, recurrence="none", channel="voice"):
    seg = (await client.post(f"{M}/audience/segments", json={"name": "V"}, headers=H)).json()
    if members:
        await client.post(f"{M}/audience/segments/{seg['id']}/members", json={"members": members}, headers=H)
    camp = (await client.post(f"{M}/campaigns", json={"name": "Calls", "channel": channel, "audience_segment_id": seg["id"]}, headers=H)).json()
    await client.patch(f"{M}/campaigns/{camp['id']}/status", json={"status": "active"}, headers=H)
    draft = (await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": channel, "body_text": "Hello from VOYQI", "subject_line": "Hi"}, headers=H)).json()
    at = (when or datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    return camp, (await client.post(f"{M}/sends", json={"campaign_id": camp["id"], "content_draft_id": draft["id"], "channel": channel, "scheduled_at": at, "recurrence": recurrence}, headers=H)).json()


async def test_voice_sends_need_consent_and_the_calling_window(client, factory, sent, window):
    _c, send = await _voice_send(client, members=[{"name": "Yes", "phone": "+1001", "consent_voice": True}, {"name": "No", "phone": "+1002"}])
    url = f"{M}/sends/{send['id']}/execute"
    resp = await client.post(url, json={"recipient_contacts": ["+1003"]}, headers=H)
    assert resp.status_code == 422 and "consent" in resp.json()["detail"]
    window["open"] = False
    assert (await client.post(url, json={}, headers=H)).status_code == 409
    assert await scheduler.run_once(factory) == 0  # rescheduled to the next window, not sent
    moved = {s["id"]: s for s in (await client.get(f"{M}/sends", headers=H)).json()}[send["id"]]
    assert moved["status"] == "scheduled" and moved["scheduled_at"] > send["scheduled_at"]
    window["open"] = True
    done = (await client.post(url, json={}, headers=H)).json()
    assert done["status"] == "sent" and [c["recipient"] for c in sent] == ["+1001"]


async def test_channel_settings_validation(client):
    assert (await client.get(f"{M}/settings", headers=H)).json()["voice_window_start"] == 9
    assert (await client.put(f"{M}/settings", json={"voice_window_start": 6}, headers=H)).status_code == 422
    assert (await client.put(f"{M}/settings", json={"timezone": "Mars/Base"}, headers=H)).status_code == 422
    ok = (await client.put(f"{M}/settings", json={"timezone": "Asia/Kolkata", "voice_window_start": 10, "voice_window_end": 19}, headers=H)).json()
    assert (ok["timezone"], ok["voice_window_start"], ok["voice_window_end"]) == ("Asia/Kolkata", 10, 19)


async def test_recurring_sends_and_posts_book_their_next_occurrence(client, factory, sent):
    _c, send = await _voice_send(client, members=[{"email": "a@x.com"}], channel="email", recurrence="daily")
    await scheduler.run_once(factory)
    sends = sorted((await client.get(f"{M}/sends", headers=H)).json(), key=lambda s: s["scheduled_at"])
    assert [s["status"] for s in sends] == ["sent", "scheduled"] and sends[1]["recurrence"] == "daily"
    assert datetime.fromisoformat(sends[1]["scheduled_at"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    camp = (await client.post(f"{M}/campaigns", json={"name": "Reel", "channel": "social"}, headers=H)).json()
    draft = (await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": "social", "body_text": "Weekly #VOYQI"}, headers=H)).json()
    acct = (await client.post(f"{M}/social/accounts", json={"platform": "facebook", "handle": "@v"}, headers=H)).json()
    post = (await client.post(f"{M}/social/posts", json={"social_account_id": acct["id"], "content_draft_id": draft["id"], "scheduled_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), "recurrence": "weekly"}, headers=H)).json()
    await client.post(f"{M}/social/posts/{post['id']}/publish", json={}, headers=H)
    posts = sorted((await client.get(f"{M}/social/posts", headers=H)).json(), key=lambda p: p["scheduled_at"])
    assert [p["status"] for p in posts] == ["published", "scheduled"]
    bad = await client.post(f"{M}/sends", json={"campaign_id": camp["id"], "content_draft_id": draft["id"], "channel": "email", "scheduled_at": "2026-12-01T10:00:00Z", "recurrence": "hourly"}, headers=H)
    assert bad.status_code == 422


def test_three_times_a_week_lands_on_monday_wednesday_friday():
    friday = datetime(2026, 10, 30, 16, tzinfo=timezone.utc)
    assert [channel_policy.next_occurrence(d, "3x_week").weekday() for d in (friday, friday + timedelta(days=3), friday + timedelta(days=5))] == [0, 2, 4]


async def test_report_and_csv(client, factory, sent):
    camp, _send = await _voice_send(client, members=[{"email": "a@x.com"}, {"email": "b@x.com"}], channel="email")
    await scheduler.run_once(factory)
    rep = (await client.get(f"{M}/reports?days=7", headers=H)).json()
    assert rep["channels"]["email"]["delivered"] == 2 and rep["daily_delivered"][-1]["delivered"] == 2
    assert rep["top_campaigns"][0]["campaign_id"] == camp["id"]
    csv = await client.get(f"{M}/reports.csv?days=7", headers=H)
    assert csv.headers["content-type"].startswith("text/csv") and "channel,email,delivered,2" in csv.text
