"""APIs the marketing console needs: listing A/B tests, and asking the
Marketing Content Agent for a draft without AI admin permissions."""
import pytest

from src.services import ai_drafting
from tests.conftest import TENANT_HEADERS


async def _campaign(client, name="Spring EV push", channel="email"):
    return (await client.post("/api/v1/marketing/campaigns", json={"name": name, "channel": channel, "objective": "Drive EV subscriptions"}, headers=TENANT_HEADERS)).json()


async def _draft(client, campaign_id, text):
    return (await client.post("/api/v1/marketing/content", json={"campaign_id": campaign_id, "channel": "email", "body_text": text, "subject_line": "Hi"}, headers=TENANT_HEADERS)).json()


async def test_ab_tests_are_listed_per_tenant_and_campaign(client):
    one, two = await _campaign(client), await _campaign(client, "Other")
    a, b = await _draft(client, one["id"], "Version A"), await _draft(client, one["id"], "Version B")
    await client.post("/api/v1/marketing/ab-tests", json={"campaign_id": one["id"], "variant_a_draft_id": a["id"], "variant_b_draft_id": b["id"]}, headers=TENANT_HEADERS)
    assert len((await client.get("/api/v1/marketing/ab-tests", headers=TENANT_HEADERS)).json()) == 1
    assert (await client.get(f"/api/v1/marketing/ab-tests?campaign_id={two['id']}", headers=TENANT_HEADERS)).json() == []
    assert (await client.get("/api/v1/marketing/ab-tests", headers={"X-Tenant-Id": "someone-else"})).json() == []


async def test_ai_draft_passes_the_campaign_to_the_agent(client, monkeypatch):
    calls = []

    async def fake_generate(tenant_id, campaign_id, campaign_name, channel, brief):
        calls.append((tenant_id, campaign_id, campaign_name, channel, brief))
        return {"draft": {"id": "d1", "status": "approved"}, "skipped_reason": None, "decision_log_id": "x"}

    monkeypatch.setattr(ai_drafting, "generate", fake_generate)
    campaign = await _campaign(client)
    resp = await client.post("/api/v1/marketing/content/ai-draft", json={"campaign_id": campaign["id"]}, headers=TENANT_HEADERS)
    assert resp.status_code == 200 and resp.json()["draft"]["status"] == "approved"
    assert calls == [(TENANT_HEADERS["X-Tenant-Id"], campaign["id"], "Spring EV push", "email", "Drive EV subscriptions")]


@pytest.mark.parametrize("outcome,status", [("paused", 409), ("unreachable", 502)])
async def test_ai_draft_reports_a_paused_or_unreachable_agent(client, monkeypatch, outcome, status):
    async def fake_generate(*args):
        if outcome == "unreachable":
            raise ai_drafting.AIDraftUnavailable("down")
        return {"draft": None, "skipped_reason": "paused"}

    monkeypatch.setattr(ai_drafting, "generate", fake_generate)
    campaign = await _campaign(client)
    resp = await client.post("/api/v1/marketing/content/ai-draft", json={"campaign_id": campaign["id"], "brief": "x"}, headers=TENANT_HEADERS)
    assert resp.status_code == status


async def test_ai_draft_needs_a_real_campaign(client):
    resp = await client.post("/api/v1/marketing/content/ai-draft", json={"campaign_id": "00000000-0000-0000-0000-000000000000"}, headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def _scheduled_send(client):
    campaign = await _campaign(client)
    draft = await _draft(client, campaign["id"], "Meet the new Model Y subscription")
    assert draft["status"] == "approved"
    send = (await client.post("/api/v1/marketing/sends", json={"campaign_id": campaign["id"], "content_draft_id": draft["id"], "channel": "email", "scheduled_at": "2026-12-01T10:00:00Z"}, headers=TENANT_HEADERS)).json()
    return campaign, send


async def test_executing_a_send_records_what_was_delivered(client):
    campaign, send = await _scheduled_send(client)
    await client.post("/api/v1/marketing/suppression", json={"channel": "email", "contact_identifier": "out@x.com", "reason": "opt_out"}, headers=TENANT_HEADERS)
    done = (await client.post(f"/api/v1/marketing/sends/{send['id']}/execute", json={"recipient_contacts": ["a@x.com", "b@x.com", "out@x.com"]}, headers=TENANT_HEADERS)).json()
    assert (done["status"], done["recipients_suppressed"]) == ("sent", 1)
    summary = (await client.get(f"/api/v1/marketing/analytics/campaigns/{campaign['id']}/summary", headers=TENANT_HEADERS)).json()
    assert summary["total_sent"] == 2


async def test_a_send_cannot_be_executed_twice(client):
    _campaign_, send = await _scheduled_send(client)
    url = f"/api/v1/marketing/sends/{send['id']}/execute"
    assert (await client.post(url, json={"recipient_contacts": ["a@x.com"]}, headers=TENANT_HEADERS)).status_code == 200
    again = await client.post(url, json={"recipient_contacts": ["a@x.com"]}, headers=TENANT_HEADERS)
    assert again.status_code == 409 and "already sent" in again.json()["detail"]
