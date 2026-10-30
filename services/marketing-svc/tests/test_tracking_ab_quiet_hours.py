"""Opens and clicks are tracked per message and feed analytics and A/B
tests; tracked links can't be forged; marketing texts wait out quiet hours."""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from src.core.config import get_settings
from src.services import channel_policy
from tests.conftest import TENANT_HEADERS
from tests.test_delivery_and_scheduler import notif  # noqa: F401 (fixture)

M = "/api/v1/marketing"
H = TENANT_HEADERS


@pytest.fixture
def tracked(monkeypatch):
    monkeypatch.setattr(get_settings(), "tracking_enabled", True)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://go.voyqi.test")


async def _campaign(client, channel, members, body, subject="Hi"):
    seg = (await client.post(f"{M}/audience/segments", json={"name": "S"}, headers=H)).json()
    await client.post(f"{M}/audience/segments/{seg['id']}/members", json={"members": members}, headers=H)
    camp = (await client.post(f"{M}/campaigns", json={"name": "C", "channel": channel, "audience_segment_id": seg["id"]}, headers=H)).json()
    await client.patch(f"{M}/campaigns/{camp['id']}/status", json={"status": "active"}, headers=H)
    draft = (await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": channel, "body_text": body, "subject_line": subject}, headers=H)).json()
    return camp, draft


async def _send(client, camp, draft, channel):
    at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    send = (await client.post(f"{M}/sends", json={"campaign_id": camp["id"], "content_draft_id": draft["id"], "channel": channel, "scheduled_at": at}, headers=H)).json()
    return await client.post(f"{M}/sends/{send['id']}/execute", json={}, headers=H)


def _links(body):
    return re.findall(r'https://go\.voyqi\.test/api/v1/marketing/public/t/[^"\s<]+', body.replace("&amp;", "&"))


def _path(url):
    p = urlparse(url)
    return p.path + ("?" + p.query if p.query else "")


async def test_opens_and_clicks_are_recorded_once_per_message(client, notif, tracked):  # noqa: F811
    camp, draft = await _campaign(client, "email", [{"name": "Ava", "email": "ava@x.com"}], "Plans: https://voyqi.com/plans")
    await _send(client, camp, draft, "email")
    body = notif["calls"][0]["body"]
    assert body.startswith('<div style="white-space:pre-wrap') and "https://voyqi.com/plans" in body
    pixel = [u for u in _links(body) if "/o/" in u][0]
    click = [u for u in _links(body) if "/c/" in u][0]
    for _ in range(2):
        r = await client.get(_path(pixel))
        assert r.status_code == 200 and r.headers["content-type"] == "image/gif"
    r = await client.get(_path(click), follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://voyqi.com/plans"
    summary = (await client.get(f"{M}/analytics/campaigns/{camp['id']}/summary", headers=H)).json()
    assert summary["total_open"] == 1 and summary["total_click"] == 1 and summary["total_sent"] == 1


async def test_tracked_links_cannot_be_forged_or_redirected_elsewhere(client, notif, tracked):  # noqa: F811
    camp, draft = await _campaign(client, "email", [{"email": "ava@x.com"}], "See https://voyqi.com/a")
    await _send(client, camp, draft, "email")
    click = [u for u in _links(notif["calls"][0]["body"]) if "/c/" in u][0]
    parsed = urlparse(click)
    tok, sig = parsed.path.rsplit("/", 1)[-1], parse_qs(parsed.query)["s"][0]
    assert (await client.get(f"{parsed.path}?u=https://evil.example&s={sig}", follow_redirects=False)).status_code == 404
    forged = tok.split(".")[0] + ".000000000000000000000000"
    assert (await client.get(f"{M}/public/t/c/{forged}?{parsed.query}", follow_redirects=False)).status_code == 404
    assert (await client.get(f"{M}/public/t/o/garbage.gif")).status_code == 200  # still a pixel; records nothing


async def test_sms_links_are_tracked(client, notif, tracked):  # noqa: F811
    camp, draft = await _campaign(client, "sms", [{"phone": "+15125550101"}], "Book: https://voyqi.com/b")
    await _send(client, camp, draft, "sms")
    body = notif["calls"][0]["body"]
    assert body.startswith("Book: https://go.voyqi.test/api/v1/marketing/public/t/c/") and body.endswith("Reply STOP to opt out.")


async def test_ab_test_splits_the_audience_and_picks_a_winner_from_opens(client, notif, tracked, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "ab_test_min_sample_per_arm", 20)
    camp, a = await _campaign(client, "email", [{"email": f"p{i}@x.com"} for i in range(120)], "Hello", "Subject A")
    b = (await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": "email", "body_text": "Hello", "subject_line": "Subject B"}, headers=H)).json()
    test = (await client.post(f"{M}/ab-tests", json={"campaign_id": camp["id"], "variant_a_draft_id": a["id"], "variant_b_draft_id": b["id"], "metric": "open_rate"}, headers=H)).json()
    await _send(client, camp, a, "email")
    by_subject: dict[str, list] = {}
    for c in notif["calls"]:
        by_subject.setdefault(c["subject"], []).append(c)
    assert set(by_subject) == {"Subject A", "Subject B"} and 35 < len(by_subject["Subject A"]) < 85
    t = [x for x in (await client.get(f"{M}/ab-tests", headers=H)).json() if x["id"] == test["id"]][0]
    assert t["variant_a_sent"] + t["variant_b_sent"] == 120
    for c in by_subject["Subject B"]:  # everyone who got B opens it; nobody opens A
        await client.get(_path([u for u in _links(c["body"]) if "/o/" in u][0]))
    t = [x for x in (await client.get(f"{M}/ab-tests", headers=H)).json() if x["id"] == test["id"]][0]
    assert t["status"] == "concluded" and t["winner_draft_id"] == b["id"]


async def test_best_send_times_from_real_opens(client, notif, tracked):  # noqa: F811
    camp, draft = await _campaign(client, "email", [{"email": f"q{i}@x.com"} for i in range(25)], "Hi")
    await _send(client, camp, draft, "email")
    for c in notif["calls"][:10]:
        await client.get(_path([u for u in _links(c["body"]) if "/o/" in u][0]))
    st = (await client.get(f"{M}/analytics/send-times", headers=H)).json()
    assert st["messages"] == 25 and st["best"][0]["open_rate"] == 0.4


async def test_texts_wait_out_quiet_hours(client, notif, monkeypatch):  # noqa: F811
    monkeypatch.setattr(channel_policy, "in_sms_window", lambda settings, now=None: False)
    camp, draft = await _campaign(client, "sms", [{"phone": "+15125550101"}], "Hi")
    r = await _send(client, camp, draft, "sms")
    assert r.status_code == 409 and "quiet hours" in r.json()["detail"] and notif["calls"] == []


def test_sms_window_rules(monkeypatch):
    monkeypatch.undo()  # the real window check, not the autouse stand-in

    class S:
        timezone, sms_window_start, sms_window_end = "UTC", 9, 21

    assert channel_policy.in_sms_window(S, datetime(2026, 1, 1, 12, tzinfo=timezone.utc))
    assert not channel_policy.in_sms_window(S, datetime(2026, 1, 1, 22, tzinfo=timezone.utc))
    assert channel_policy.next_window_start(S, datetime(2026, 1, 1, 22, tzinfo=timezone.utc), start_hour=9) == datetime(2026, 1, 2, 9, tzinfo=timezone.utc)


async def test_quiet_hours_are_validated(client):
    assert (await client.put(f"{M}/settings", json={"sms_window_start": 7, "sms_window_end": 21}, headers=H)).status_code == 422
    r = (await client.put(f"{M}/settings", json={"sms_window_start": 10, "sms_window_end": 20}, headers=H)).json()
    assert (r["sms_window_start"], r["sms_window_end"]) == (10, 20)
