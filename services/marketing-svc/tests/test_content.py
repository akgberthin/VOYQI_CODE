from tests.conftest import TENANT_HEADERS


async def _create_campaign(client, channel="email"):
    resp = await client.post("/api/v1/marketing/campaigns", json={"name": "Test Campaign", "channel": channel}, headers=TENANT_HEADERS)
    return resp.json()


async def test_clean_draft_auto_approved(client):
    campaign = await _create_campaign(client)
    resp = await client.post("/api/v1/marketing/content", json={
        "campaign_id": campaign["id"], "channel": "email", "subject_line": "New classes available", "body_text": "Explore our latest mobility classes.",
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "approved"
    assert body["guardrail_findings"] == []


async def test_flagged_claim_language_held_for_review(client):
    campaign = await _create_campaign(client)
    resp = await client.post("/api/v1/marketing/content", json={
        "campaign_id": campaign["id"], "channel": "email", "subject_line": "Guaranteed savings", "body_text": "This is a guaranteed deal, risk-free for you.",
    }, headers=TENANT_HEADERS)
    body = resp.json()
    assert body["status"] == "held_for_review"
    assert len(body["guardrail_findings"]) == 2


async def test_email_subject_over_length_limit_held(client):
    campaign = await _create_campaign(client)
    long_subject = "A" * 200
    resp = await client.post("/api/v1/marketing/content", json={
        "campaign_id": campaign["id"], "channel": "email", "subject_line": long_subject, "body_text": "Body text here.",
    }, headers=TENANT_HEADERS)
    body = resp.json()
    assert body["status"] == "held_for_review"
    assert any("subject line" in f for f in body["guardrail_findings"])


async def test_sms_over_length_limit_held(client):
    campaign = await _create_campaign(client, channel="sms")
    long_body = "A" * 400
    resp = await client.post("/api/v1/marketing/content", json={
        "campaign_id": campaign["id"], "channel": "sms", "body_text": long_body,
    }, headers=TENANT_HEADERS)
    body = resp.json()
    assert body["status"] == "held_for_review"
    assert any("SMS limit" in f for f in body["guardrail_findings"])


async def test_empty_body_held(client):
    campaign = await _create_campaign(client)
    resp = await client.post("/api/v1/marketing/content", json={
        "campaign_id": campaign["id"], "channel": "email", "subject_line": "Hi", "body_text": "   ",
    }, headers=TENANT_HEADERS)
    body = resp.json()
    assert body["status"] == "held_for_review"


async def test_human_approve_clears_held_draft(client):
    campaign = await _create_campaign(client)
    draft = (await client.post("/api/v1/marketing/content", json={
        "campaign_id": campaign["id"], "channel": "email", "body_text": "guaranteed results",
    }, headers=TENANT_HEADERS)).json()
    assert draft["status"] == "held_for_review"

    approved = (await client.post(f"/api/v1/marketing/content/{draft['id']}/approve", json={"reviewed_by": "compliance-1"}, headers=TENANT_HEADERS)).json()
    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == "compliance-1"


async def test_reject_draft(client):
    campaign = await _create_campaign(client)
    draft = (await client.post("/api/v1/marketing/content", json={
        "campaign_id": campaign["id"], "channel": "email", "body_text": "Clean copy here.",
    }, headers=TENANT_HEADERS)).json()
    rejected = (await client.post(f"/api/v1/marketing/content/{draft['id']}/reject", json={"reviewed_by": "editor-1"}, headers=TENANT_HEADERS)).json()
    assert rejected["status"] == "rejected"


async def test_human_override_of_held_draft_publishes_for_compliance(client, monkeypatch):
    """content_draft.approved_with_violations routes to the
    Compliance/Audit Agent, which scores on violation_count."""
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append((event_type, payload))

    monkeypatch.setattr("src.api.content.publish_event", _spy)
    campaign = await _create_campaign(client)
    held = (await client.post("/api/v1/marketing/content", json={
        "campaign_id": campaign["id"], "channel": "email", "subject_line": "Guaranteed savings", "body_text": "This is a guaranteed deal, risk-free for you.",
    }, headers=TENANT_HEADERS)).json()

    await client.post(f"/api/v1/marketing/content/{held['id']}/approve", json={"reviewed_by": "mkt-lead"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/marketing/content/{held['id']}/approve", json={"reviewed_by": "mkt-lead"}, headers=TENANT_HEADERS)

    [(event_type, payload)] = events
    assert event_type == "content_draft.approved_with_violations"
    assert payload["draft_id"] == held["id"]
    assert payload["violation_count"] == 2
    assert payload["reviewed_by"] == "mkt-lead"
