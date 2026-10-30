"""Provider outcomes come back from notification-svc: answered calls count
as pickups, unanswered ones are called again (within the calling window,
never after an opt-out), delivered/undelivered texts are recorded."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.services import channel_options, channel_policy, delivery, tracking
from tests.conftest import TENANT_HEADERS

M = "/api/v1/marketing"
H = TENANT_HEADERS


@pytest.fixture
def provider(monkeypatch):
    """notification-svc stand-in: real delivery with ids, and reports we set."""
    state = {"calls": [], "reports": {}, "n": 0}

    async def deliver(tenant_id, channel, recipient, body, subject, source, options=None):
        state["n"] += 1
        nid = f"00000000-0000-4000-8000-{state['n']:012d}"
        state["calls"].append({"to": recipient, "body": body, "source": source, "options": options, "id": nid})
        return {"status": "sent", "delivery_mode": "real_provider", "id": nid, "error_message": None}

    async def statuses(tenant_id, ids):
        return [{"id": i, **state["reports"][i]} for i in ids if i in state["reports"]]

    monkeypatch.setattr(delivery, "deliver", deliver)
    monkeypatch.setattr(delivery, "statuses", statuses)
    monkeypatch.setattr(channel_policy, "in_voice_window", lambda settings, now=None: True)
    return state


async def _voice_send(client, members, options):
    seg = (await client.post(f"{M}/audience/segments", json={"name": "S"}, headers=H)).json()
    await client.post(f"{M}/audience/segments/{seg['id']}/members", json={"members": members}, headers=H)
    camp = (await client.post(f"{M}/campaigns", json={"name": "C", "channel": "voice", "audience_segment_id": seg["id"]}, headers=H)).json()
    await client.patch(f"{M}/campaigns/{camp['id']}/status", json={"status": "active"}, headers=H)
    draft = (await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": "voice", "body_text": "Hello {first_name}", "options": options}, headers=H)).json()
    send = (await client.post(f"{M}/sends", json={"campaign_id": camp["id"], "content_draft_id": draft["id"], "channel": "voice",
                                                  "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, headers=H)).json()
    r = await client.post(f"{M}/sends/{send['id']}/execute", json={}, headers=H)
    assert r.status_code == 200, r.text
    return camp, send


def test_retry_options_are_checked():
    assert channel_options.validate("voice", {"max_attempts": 3, "retry_hours": 4, "record": True}) == {"max_attempts": 3, "retry_hours": 4, "record": True}
    for bad in ({"max_attempts": 5}, {"retry_hours": 3}, {"record": "yes"}):
        with pytest.raises(channel_options.InvalidOptions):
            channel_options.validate("voice", bad)


async def test_unanswered_calls_are_retried_and_answered_ones_count_as_pickups(client, provider, db_engine):
    people = [{"name": "Ava", "phone": "+15125550101", "consent_voice": True}, {"name": "Noah", "phone": "+15125550102", "consent_voice": True}]
    camp, send = await _voice_send(client, people, {"max_attempts": 2, "retry_hours": 2, "record": True})
    ava, noah = provider["calls"]
    provider["reports"][ava["id"]] = {"provider_status": "completed", "answered_by": "human", "recording_url": "https://api.twilio.com/rec/1"}
    provider["reports"][noah["id"]] = {"provider_status": "no-answer"}
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    assert await tracking.sync_outcomes(factory, now) == 2

    out = (await client.get(f"{M}/analytics/sends/{send['id']}/outcomes", headers=H)).json()
    assert out["outcomes"] == {"answered": 1, "no-answer": 1} and out["retries_scheduled"] == 1
    assert out["recordings"] == [{"contact": "+15125550101", "url": "https://api.twilio.com/rec/1"}]
    summary = (await client.get(f"{M}/analytics/campaigns/{camp['id']}/summary", headers=H)).json()
    assert summary["total_open"] == 1  # the pickup

    assert await tracking.retry_due(factory, now + timedelta(hours=1)) == 0  # not yet
    assert await tracking.retry_due(factory, now + timedelta(hours=3)) == 1
    retry = provider["calls"][-1]
    assert retry["to"] == "+15125550102" and retry["body"] == "Hello Noah" and retry["source"].endswith(":retry")
    provider["reports"][retry["id"]] = {"provider_status": "busy"}
    await tracking.sync_outcomes(factory, now + timedelta(hours=4))
    out = (await client.get(f"{M}/analytics/sends/{send['id']}/outcomes", headers=H)).json()
    assert out["retries_scheduled"] == 0 and out["attempts"] == 3  # two attempts used for Noah: no more


async def test_no_retry_after_an_opt_out_and_only_inside_the_window(client, provider, db_engine, monkeypatch):
    _camp, send = await _voice_send(client, [{"name": "Mia", "phone": "+15125550103", "consent_voice": True}], {"max_attempts": 3, "retry_hours": 1})
    provider["reports"][provider["calls"][0]["id"]] = {"provider_status": "completed", "answered_by": "machine_end_beep"}
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    await tracking.sync_outcomes(factory, now)
    monkeypatch.setattr(channel_policy, "in_voice_window", lambda settings, now=None: False)
    assert await tracking.retry_due(factory, now + timedelta(hours=2)) == 0  # waits for the window
    monkeypatch.setattr(channel_policy, "in_voice_window", lambda settings, now=None: True)
    await client.post(f"{M}/suppression", json={"channel": "voice", "contact_identifier": "+15125550103", "reason": "opt_out"}, headers=H)
    assert await tracking.retry_due(factory, now + timedelta(days=2)) == 0
    out = (await client.get(f"{M}/analytics/sends/{send['id']}/outcomes", headers=H)).json()
    assert out["outcomes"] == {"suppressed": 1} and len(provider["calls"]) == 1


async def test_texts_record_delivered_and_undelivered(client, provider, db_engine):
    seg = (await client.post(f"{M}/audience/segments", json={"name": "S"}, headers=H)).json()
    await client.post(f"{M}/audience/segments/{seg['id']}/members", json={"members": [{"phone": "+15125550104"}, {"phone": "+15125550105"}]}, headers=H)
    camp = (await client.post(f"{M}/campaigns", json={"name": "C", "channel": "sms", "audience_segment_id": seg["id"]}, headers=H)).json()
    await client.patch(f"{M}/campaigns/{camp['id']}/status", json={"status": "active"}, headers=H)
    draft = (await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": "sms", "body_text": "Hi"}, headers=H)).json()
    send = (await client.post(f"{M}/sends", json={"campaign_id": camp["id"], "content_draft_id": draft["id"], "channel": "sms",
                                                  "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, headers=H)).json()
    await client.post(f"{M}/sends/{send['id']}/execute", json={}, headers=H)
    a, b = provider["calls"]
    provider["reports"][a["id"]] = {"provider_status": "delivered"}
    provider["reports"][b["id"]] = {"provider_status": "undelivered"}
    await tracking.sync_outcomes(async_sessionmaker(db_engine, expire_on_commit=False))
    out = (await client.get(f"{M}/analytics/sends/{send['id']}/outcomes", headers=H)).json()
    assert out["outcomes"] == {"delivered": 1, "undelivered": 1}
