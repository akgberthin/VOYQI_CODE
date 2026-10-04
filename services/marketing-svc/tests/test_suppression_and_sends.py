from tests.conftest import TENANT_HEADERS


async def _setup_approved_draft(client, channel="email"):
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "Test", "channel": channel}, headers=TENANT_HEADERS)).json()
    draft = (await client.post("/api/v1/marketing/content", json={"campaign_id": campaign["id"], "channel": channel, "body_text": "Clean copy here."}, headers=TENANT_HEADERS)).json()
    assert draft["status"] == "approved"
    return campaign, draft


async def test_scheduling_unapproved_draft_refused(client):
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "Test", "channel": "email"}, headers=TENANT_HEADERS)).json()
    bad_draft = (await client.post("/api/v1/marketing/content", json={"campaign_id": campaign["id"], "channel": "email", "body_text": "guaranteed savings"}, headers=TENANT_HEADERS)).json()
    resp = await client.post("/api/v1/marketing/sends", json={
        "campaign_id": campaign["id"], "content_draft_id": bad_draft["id"], "channel": "email", "scheduled_at": "2026-08-15T10:00:00Z",
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_scheduling_approved_draft_succeeds_and_populates_calendar(client):
    campaign, draft = await _setup_approved_draft(client)
    resp = await client.post("/api/v1/marketing/sends", json={
        "campaign_id": campaign["id"], "content_draft_id": draft["id"], "channel": "email", "scheduled_at": "2026-08-15T10:00:00Z",
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["status"] == "scheduled"

    calendar = (await client.get("/api/v1/marketing/calendar", headers=TENANT_HEADERS)).json()
    assert len(calendar) == 1
    assert calendar[0]["reference_type"] == "scheduled_send"


async def test_suppressed_contact_never_counted_as_sent(client):
    campaign, draft = await _setup_approved_draft(client)
    send = (await client.post("/api/v1/marketing/sends", json={
        "campaign_id": campaign["id"], "content_draft_id": draft["id"], "channel": "email", "scheduled_at": "2026-08-15T10:00:00Z",
    }, headers=TENANT_HEADERS)).json()

    await client.post("/api/v1/marketing/suppression", json={"channel": "email", "contact_identifier": "opted-out@test.com", "reason": "opt_out"}, headers=TENANT_HEADERS)

    executed = (await client.post(f"/api/v1/marketing/sends/{send['id']}/execute", json={
        "recipient_contacts": ["clean@test.com", "opted-out@test.com"],
    }, headers=TENANT_HEADERS)).json()
    assert executed["status"] == "sent"
    assert executed["recipients_targeted"] == 2
    assert executed["recipients_suppressed"] == 1


async def test_all_recipients_suppressed_blocks_the_send_entirely(client):
    campaign, draft = await _setup_approved_draft(client, channel="sms")
    send = (await client.post("/api/v1/marketing/sends", json={
        "campaign_id": campaign["id"], "content_draft_id": draft["id"], "channel": "sms", "scheduled_at": "2026-08-15T10:00:00Z",
    }, headers=TENANT_HEADERS)).json()

    await client.post("/api/v1/marketing/suppression", json={"channel": "sms", "contact_identifier": "+15551234567", "reason": "opt_out"}, headers=TENANT_HEADERS)

    executed = (await client.post(f"/api/v1/marketing/sends/{send['id']}/execute", json={
        "recipient_contacts": ["+15551234567"],
    }, headers=TENANT_HEADERS)).json()
    assert executed["status"] == "blocked_suppression"


async def test_suppression_check_endpoint(client):
    await client.post("/api/v1/marketing/suppression", json={"channel": "email", "contact_identifier": "bounced@test.com", "reason": "bounce"}, headers=TENANT_HEADERS)
    resp = (await client.get("/api/v1/marketing/suppression/check", params={"channel": "email", "contact_identifier": "bounced@test.com"}, headers=TENANT_HEADERS)).json()
    assert resp["suppressed"] is True
    assert resp["reason"] == "bounce"

    clean = (await client.get("/api/v1/marketing/suppression/check", params={"channel": "email", "contact_identifier": "clean@test.com"}, headers=TENANT_HEADERS)).json()
    assert clean["suppressed"] is False


async def test_suppression_channel_is_specific(client):
    """A suppression on one channel must not suppress a different
    channel for the same contact identifier."""
    await client.post("/api/v1/marketing/suppression", json={"channel": "sms", "contact_identifier": "shared-contact", "reason": "opt_out"}, headers=TENANT_HEADERS)
    sms_check = (await client.get("/api/v1/marketing/suppression/check", params={"channel": "sms", "contact_identifier": "shared-contact"}, headers=TENANT_HEADERS)).json()
    email_check = (await client.get("/api/v1/marketing/suppression/check", params={"channel": "email", "contact_identifier": "shared-contact"}, headers=TENANT_HEADERS)).json()
    assert sms_check["suppressed"] is True
    assert email_check["suppressed"] is False


async def test_remove_from_suppression_list(client):
    entry = (await client.post("/api/v1/marketing/suppression", json={"channel": "email", "contact_identifier": "re-subscribed@test.com", "reason": "opt_out"}, headers=TENANT_HEADERS)).json()
    resp = await client.delete(f"/api/v1/marketing/suppression/{entry['id']}", headers=TENANT_HEADERS)
    assert resp.json()["removed"] is True
    check = (await client.get("/api/v1/marketing/suppression/check", params={"channel": "email", "contact_identifier": "re-subscribed@test.com"}, headers=TENANT_HEADERS)).json()
    assert check["suppressed"] is False
