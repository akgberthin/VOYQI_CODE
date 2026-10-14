"""The exact calls ai-orchestration-svc's crm_tools makes, plus the
human actions that resolve what the agents propose."""
import pytest

from tests.conftest import TENANT_HEADERS as H


@pytest.fixture
def published(monkeypatch):
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append((event_type, payload))

    monkeypatch.setattr("src.api.crm_intelligence.publish_event", _spy)
    return events


# --- leads (CRM-002) --------------------------------------------------------


async def test_agent_creates_lead_with_full_name_and_no_email(client):
    """crm_tools.create_lead sends full_name and may send email=""."""
    resp = await client.post(
        "/api/v1/crm/leads",
        json={"source": "web_landing_page", "full_name": "Ana Maria Lopez", "email": "", "phone": "555-0101", "external_ref_id": "lc-1"},
        headers=H,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert (body["first_name"], body["last_name"]) == ("Ana", "Maria Lopez")
    assert body["external_ref_id"] == "lc-1"
    assert body["score"] is None


async def test_lead_needs_a_name_and_a_contact(client):
    assert (await client.post("/api/v1/crm/leads", json={"email": "a@x.com"}, headers=H)).status_code == 422
    assert (await client.post("/api/v1/crm/leads", json={"full_name": "Ana"}, headers=H)).status_code == 422


async def test_score_write_back_is_returned_by_lead_lookup(client):
    """org_tools/revenue_agent get_lead_score reads score + routed_to_team."""
    lead = (await client.post("/api/v1/crm/leads", json={"first_name": "Jo", "last_name": "Ray", "email": "jo@x.com"}, headers=H)).json()
    resp = await client.post(f"/api/v1/crm/leads/{lead['id']}/score", json={"score": 0.82, "reasoning": "hot", "routed_to_team": "enterprise_sales"}, headers=H)
    assert resp.status_code == 200

    fetched = (await client.get(f"/api/v1/crm/leads/{lead['id']}", headers=H)).json()
    assert fetched["score"] == 0.82
    assert fetched["routed_to_team"] == "enterprise_sales"


async def test_score_out_of_range_rejected(client):
    lead = (await client.post("/api/v1/crm/leads", json={"first_name": "Jo", "last_name": "Ray", "email": "jo@x.com"}, headers=H)).json()
    resp = await client.post(f"/api/v1/crm/leads/{lead['id']}/score", json={"score": 3, "reasoning": "r", "routed_to_team": "t"}, headers=H)
    assert resp.status_code == 422


async def test_duplicate_check_matches_email_or_phone(client):
    await client.post("/api/v1/crm/leads", json={"first_name": "Jo", "last_name": "Ray", "email": "Jo@X.com"}, headers=H)
    await client.post("/api/v1/crm/leads", json={"first_name": "Al", "last_name": "B", "phone": "555-0199"}, headers=H)

    by_email = (await client.get("/api/v1/crm/leads/duplicates/check", params={"email": "jo@x.com"}, headers=H)).json()
    by_phone = (await client.get("/api/v1/crm/leads/duplicates/check", params={"phone": "555-0199"}, headers=H)).json()
    none = (await client.get("/api/v1/crm/leads/duplicates/check", params={"email": "", "phone": ""}, headers=H)).json()
    assert [lead["first_name"] for lead in by_email] == ["Jo"]
    assert [lead["first_name"] for lead in by_phone] == ["Al"]
    assert none == []


# --- identity (CRM-001, CRM-007) -------------------------------------------


async def test_source_record_publishes_event_for_identity_resolution(client, published):
    resp = await client.post(
        "/api/v1/crm/source-records", json={"source_system": "rental_booking", "full_name": "Ana Lopez", "email": "ANA@x.com"}, headers=H
    )
    assert resp.status_code == 201
    [(event_type, payload)] = published
    assert event_type == "crm.source_record_created"
    assert payload == {"source_record_id": resp.json()["id"], "source_system": "rental_booking", "full_name": "Ana Lopez", "email": "ana@x.com", "phone": ""}


async def test_person_profile_publishes_event_and_lists_for_agent(client, published):
    profile = (await client.post("/api/v1/crm/person-profiles", json={"full_name": "Ana Lopez", "email": "ana@x.com"}, headers=H)).json()
    [(event_type, payload)] = published
    assert event_type == "crm.person_profile_created"
    assert payload["person_profile_id"] == profile["id"]

    profiles = (await client.get("/api/v1/crm/person-profiles", headers=H)).json()
    assert [p["id"] for p in profiles] == [profile["id"]]
    for key in ("id", "full_name", "email", "phone"):
        assert key in profiles[0]


async def test_confirmed_match_links_record_to_existing_profile(client, published):
    profile = (await client.post("/api/v1/crm/person-profiles", json={"full_name": "Ana Lopez", "email": "ana@x.com"}, headers=H)).json()
    record = (await client.post("/api/v1/crm/source-records", json={"source_system": "web", "full_name": "Ana L", "email": "ana@x.com"}, headers=H)).json()
    candidate = (
        await client.post(
            "/api/v1/crm/match-candidates",
            json={"source_record_id": record["id"], "candidate_person_profile_id": profile["id"], "match_score": 0.95, "match_reasoning": "email"},
            headers=H,
        )
    ).json()
    assert candidate["status"] == "pending"

    resolved = await client.post(f"/api/v1/crm/match-candidates/{candidate['id']}/resolve", json={"confirm": True, "resolved_by": "rep-1"}, headers=H)
    assert resolved.json()["status"] == "confirmed"
    again = await client.post(f"/api/v1/crm/match-candidates/{candidate['id']}/resolve", json={"confirm": True, "resolved_by": "rep-1"}, headers=H)
    assert again.status_code == 409


async def test_confirmed_no_match_creates_new_profile_and_publishes(client, published):
    record = (await client.post("/api/v1/crm/source-records", json={"source_system": "web", "full_name": "New Person", "phone": "555"}, headers=H)).json()
    candidate = (
        await client.post(
            "/api/v1/crm/match-candidates",
            json={"source_record_id": record["id"], "candidate_person_profile_id": None, "match_score": 0.0, "match_reasoning": "no match"},
            headers=H,
        )
    ).json()
    published.clear()
    await client.post(f"/api/v1/crm/match-candidates/{candidate['id']}/resolve", json={"confirm": True, "resolved_by": "rep-1"}, headers=H)

    [(event_type, payload)] = published
    assert event_type == "crm.person_profile_created"
    assert payload["full_name"] == "New Person"


async def test_match_candidate_for_unknown_record_404(client):
    resp = await client.post(
        "/api/v1/crm/match-candidates",
        json={"source_record_id": "00000000-0000-0000-0000-000000000000", "match_score": 0.1, "match_reasoning": "x"},
        headers=H,
    )
    assert resp.status_code == 404


async def test_profile_merge_hides_merged_profile(client, published):
    a = (await client.post("/api/v1/crm/person-profiles", json={"full_name": "Ana Lopez", "email": "ana@x.com"}, headers=H)).json()
    b = (await client.post("/api/v1/crm/person-profiles", json={"full_name": "Ana M Lopez", "email": "ana@x.com"}, headers=H)).json()
    dup = (
        await client.post(
            "/api/v1/crm/profile-duplicate-candidates",
            json={"profile_a_id": a["id"], "profile_b_id": b["id"], "match_score": 0.9, "match_reasoning": "same email"},
            headers=H,
        )
    ).json()
    await client.post(f"/api/v1/crm/profile-duplicate-candidates/{dup['id']}/resolve", json={"confirm": True, "resolved_by": "rep-1"}, headers=H)

    active = (await client.get("/api/v1/crm/person-profiles", headers=H)).json()
    assert [p["id"] for p in active] == [a["id"]]


async def test_profile_cannot_duplicate_itself(client, published):
    a = (await client.post("/api/v1/crm/person-profiles", json={"full_name": "Ana"}, headers=H)).json()
    resp = await client.post(
        "/api/v1/crm/profile-duplicate-candidates", json={"profile_a_id": a["id"], "profile_b_id": a["id"], "match_score": 1, "match_reasoning": "x"}, headers=H
    )
    assert resp.status_code == 422


# --- inbox (CRM-003) ---------------------------------------------------------


async def test_inbox_round_trip(client, published):
    conversation = (await client.post("/api/v1/crm/conversations", json={"channel": "email", "subject": "Billing"}, headers=H)).json()
    message = (
        await client.post(f"/api/v1/crm/conversations/{conversation['id']}/messages", json={"body": "I was double charged!", "sender": "ana@x.com"}, headers=H)
    ).json()
    [(event_type, payload)] = published
    assert event_type == "crm.message_received"
    assert payload == {"message_id": message["id"], "conversation_id": conversation["id"], "body": "I was double charged!"}

    # Concierge Agent: tag, draft, escalate.
    tagged = await client.post(f"/api/v1/crm/messages/{message['id']}/sentiment-intent", json={"sentiment": "negative", "intent": "complaint"}, headers=H)
    assert tagged.json()["intent"] == "complaint"
    draft = (
        await client.post(
            "/api/v1/crm/draft-replies",
            json={"conversation_id": conversation["id"], "in_reply_to_message_id": message["id"], "draft_body": "Sorry — refunding now.", "reasoning": "r"},
            headers=H,
        )
    ).json()
    assert draft["status"] == "pending"
    escalated = await client.post(
        f"/api/v1/crm/conversations/{conversation['id']}/escalate", json={"priority": "urgent", "routed_to_team": "support_escalations"}, headers=H
    )
    assert escalated.json()["status"] == "escalated"

    # Human sends (with an edit).
    sent = await client.post(f"/api/v1/crm/draft-replies/{draft['id']}/approve-and-send", json={"approved_by": "agent-7", "edited_body": "Refund issued."}, headers=H)
    assert sent.json()["status"] == "sent"
    history = (await client.get(f"/api/v1/crm/conversations/{conversation['id']}/messages", headers=H)).json()
    assert [(m["direction"], m["body"]) for m in history] == [("inbound", "I was double charged!"), ("outbound", "Refund issued.")]

    again = await client.post(f"/api/v1/crm/draft-replies/{draft['id']}/discard", json={"discarded_by": "x"}, headers=H)
    assert again.status_code == 409


async def test_message_to_unknown_conversation_404(client, published):
    resp = await client.post("/api/v1/crm/conversations/00000000-0000-0000-0000-000000000000/messages", json={"body": "hi"}, headers=H)
    assert resp.status_code == 404
    assert published == []


async def test_inbox_is_tenant_scoped(client, published):
    conversation = (await client.post("/api/v1/crm/conversations", json={"subject": "x"}, headers=H)).json()
    resp = await client.get(f"/api/v1/crm/conversations/{conversation['id']}/messages", headers={"X-Tenant-Id": "someone-else"})
    assert resp.status_code == 404
