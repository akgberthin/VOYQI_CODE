"""Channel settings on drafts reach delivery; contact attributes drive
targeting and personalisation; the performance report is emailed."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.services import channel_options, reports
from tests.conftest import TENANT_HEADERS
from tests.test_delivery_and_scheduler import notif  # noqa: F401 — fixture

M = "/api/v1/marketing"
H = TENANT_HEADERS

PEOPLE = [
    {"name": "Ava Patel", "email": "ava@x.com", "phone": "+15125550101", "consent_voice": True, "attributes": {"city": "Miami", "class": "luxury", "tenure_months": 14, "churn_risk": 20}},
    {"name": "Noah Kim", "email": "noah@x.com", "attributes": {"city": "Austin", "class": "urban", "tenure_months": 2, "churn_risk": 75}},
    {"name": "Mia Lopez", "phone": "+15125550102", "attributes": {"city": "Miami", "class": "urban", "tenure_months": 30, "churn_risk": 55}},
]


async def _segment(client, members, name="All"):
    seg = (await client.post(f"{M}/audience/segments", json={"name": name}, headers=H)).json()
    await client.post(f"{M}/audience/segments/{seg['id']}/members", json={"members": members}, headers=H)
    return seg


async def _send(client, channel, segment_id, body, subject="", options=None):
    camp = (await client.post(f"{M}/campaigns", json={"name": "C", "channel": channel, "audience_segment_id": segment_id}, headers=H)).json()
    await client.patch(f"{M}/campaigns/{camp['id']}/status", json={"status": "active"}, headers=H)
    r = await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": channel, "body_text": body, "subject_line": subject, "options": options or {}}, headers=H)
    assert r.status_code == 201, r.text
    send = (await client.post(f"{M}/sends", json={"campaign_id": camp["id"], "content_draft_id": r.json()["id"], "channel": channel,
                                                  "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, headers=H)).json()
    return (await client.post(f"{M}/sends/{send['id']}/execute", json={}, headers=H)).json()


async def test_options_are_checked_per_channel(client):
    camp = (await client.post(f"{M}/campaigns", json={"name": "C", "channel": "email"}, headers=H)).json()
    bad = await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": "email", "body_text": "x", "options": {"deep_link": "a"}}, headers=H)
    assert bad.status_code == 422 and "deep_link" in bad.json()["detail"]
    bad = await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": "email", "body_text": "x", "options": {"reply_to": "nope"}}, headers=H)
    assert bad.status_code == 422
    with_claim = (await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": "email", "body_text": "Hello", "subject_line": "Hi",
                                                            "options": {"preview_text": "Guaranteed savings", "from_name": "VOYQI"}}, headers=H)).json()
    assert with_claim["status"] == "held_for_review" and any("preview text" in f for f in with_claim["guardrail_findings"])
    assert with_claim["options"] == {"preview_text": "Guaranteed savings", "from_name": "VOYQI"}


def test_voice_and_language_must_agree():
    assert channel_options.validate("voice", {"voice": "Polly.Lupe", "language": "es-US"}) == {"voice": "Polly.Lupe", "language": "es-US"}
    try:
        channel_options.validate("voice", {"voice": "Polly.Lupe", "language": "en-US"})
        raise AssertionError("should refuse")
    except channel_options.InvalidOptions as exc:
        assert "speaks es-US" in str(exc)


async def test_email_options_and_attribute_tokens_reach_delivery(client, notif):  # noqa: F811
    seg = await _segment(client, PEOPLE[:2])
    await _send(client, "email", seg["id"], "Hi {first_name}, your {class} car is ready in {city}.", "For {name}",
                {"from_name": "VOYQI Concierge", "reply_to": "care@voyqi.com", "preview_text": "{city} hub update"})
    calls = {c["recipient"]: c for c in notif["calls"]}
    assert calls["ava@x.com"]["body"] == "Hi Ava, your luxury car is ready in Miami."
    assert calls["ava@x.com"]["subject"] == "For Ava Patel"
    assert calls["ava@x.com"]["options"] == {"from_name": "VOYQI Concierge", "reply_to": "care@voyqi.com", "preview_text": "Miami hub update"}
    assert calls["noah@x.com"]["body"].endswith("urban car is ready in Austin.")


async def test_sms_gets_the_opt_out_line_unless_it_has_one_or_it_is_off(client, notif):  # noqa: F811
    seg = await _segment(client, [PEOPLE[0]])
    await _send(client, "sms", seg["id"], "Your EV is ready.")
    await _send(client, "sms", seg["id"], "Ready. Text STOP to end.")
    await _send(client, "sms", seg["id"], "Transactional note.", options={"append_stop": False})
    assert [c["body"] for c in notif["calls"]] == ["Your EV is ready. Reply STOP to opt out.", "Ready. Text STOP to end.", "Transactional note."]


async def test_estimate_counts_matching_contacts_and_reach(client):
    await _segment(client, PEOPLE, "Imported")
    await _segment(client, [{"email": "AVA@x.com", "attributes": {"hub": "Brickell"}}], "Dup")  # same person, one more attribute
    r = (await client.post(f"{M}/audience/estimate", json={"filter_criteria": {"city": "miami"}}, headers=H)).json()
    assert r["total"] == 2 and r["of"] == 3 and r["reachable"] == {"email": 1, "sms": 2, "push": 0, "voice": 1}
    r = (await client.post(f"{M}/audience/estimate", json={"filter_criteria": {"churn_risk": {"min": 50}, "channel_opt_in": "email"}}, headers=H)).json()
    assert r["total"] == 1
    r = (await client.post(f"{M}/audience/estimate", json={"filter_criteria": {"class": ["luxury", "urban"], "tenure_months": {"max": 12}}}, headers=H)).json()
    assert r["total"] == 1
    bad = await client.post(f"{M}/audience/estimate", json={"filter_criteria": {"churn_risk": {"above": 3}}}, headers=H)
    assert bad.status_code == 422
    attrs = (await client.get(f"{M}/audience/attributes", headers=H)).json()
    assert attrs["city"] == ["Austin", "Miami"] and attrs["hub"] == ["Brickell"] and attrs["tenure_months"] == ["2", "14", "30"]


async def test_a_segment_can_be_filled_from_its_rules(client):
    await _segment(client, PEOPLE, "Imported")
    seg = (await client.post(f"{M}/audience/segments", json={"name": "Miami", "filter_criteria": {"city": "Miami"}, "populate": True}, headers=H)).json()
    assert seg["member_count"] == 2
    members = (await client.get(f"{M}/audience/segments/{seg['id']}/members", headers=H)).json()
    assert {m["name"] for m in members} == {"Ava Patel", "Mia Lopez"} and members[0]["attributes"]["city"] == "Miami"


async def test_report_schedule_and_email(client, notif, db_engine):  # noqa: F811
    r = await client.put(f"{M}/settings", json={"report_frequency": "weekly", "report_recipients": ["Cmo@voyqi.com", "bad"]}, headers=H)
    assert r.status_code == 422
    r = (await client.put(f"{M}/settings", json={"report_frequency": "weekly", "report_recipients": ["Cmo@voyqi.com"]}, headers=H)).json()
    assert r["report_frequency"] == "weekly" and r["report_recipients"] == ["cmo@voyqi.com"]

    sent = (await client.post(f"{M}/reports/send", json={"days": 7}, headers=H)).json()
    assert sent["sent"] == 1 and notif["calls"][-1]["recipient"] == "cmo@voyqi.com" and "last 7 days" in notif["calls"][-1]["subject"]

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    assert await reports.send_due(factory) == 1
    assert await reports.send_due(factory) == 0  # not again until next week
    assert notif["calls"][-1]["body"].startswith("VOYQI marketing report — last 7 days")


async def test_report_send_needs_someone_to_send_to(client):
    r = await client.post(f"{M}/reports/send", json={}, headers=H)
    assert r.status_code == 422 and "recipients" in r.json()["detail"]


def test_sms_and_push_options_are_checked():
    assert channel_options.validate("sms", {"sender_id": "VOYQI", "media_url": "https://cdn.voyqi.com/a.jpg"}) == {"sender_id": "VOYQI", "media_url": "https://cdn.voyqi.com/a.jpg"}
    for bad in ({"sender_id": "TOO-LONG-NAME!"}, {"sender_id": "12345"}, {"media_url": "http://x/a.jpg"}):
        try:
            channel_options.validate("sms", bad)
            raise AssertionError(f"accepted {bad}")
        except channel_options.InvalidOptions:
            pass
    assert channel_options.validate("push", {"platforms": ["ios", "ios", "android"]}) == {"platforms": ["android", "ios"]}


async def test_push_only_reaches_the_chosen_platforms(client, notif):  # noqa: F811
    seg = await _segment(client, [{"push_token": "tok-ios", "attributes": {"platform": "ios"}}, {"push_token": "tok-and", "attributes": {"platform": "android"}},
                                  {"push_token": "tok-web", "attributes": {"platform": "web"}}])
    r = await _send(client, "push", seg["id"], "Car ready", "Ready", {"platforms": ["ios", "web"]})
    assert sorted(c["recipient"] for c in notif["calls"]) == ["tok-ios", "tok-web"] and r["recipients_targeted"] == 2
