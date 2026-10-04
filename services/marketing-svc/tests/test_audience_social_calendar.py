from tests.conftest import TENANT_HEADERS


async def test_create_segment_and_set_member_count(client):
    segment = (await client.post("/api/v1/marketing/audience/segments", json={"name": "Luxury Class Members", "filter_criteria": {"mobility_class": "luxury"}}, headers=TENANT_HEADERS)).json()
    assert segment["member_count"] == 0
    updated = (await client.patch(f"/api/v1/marketing/audience/segments/{segment['id']}/member-count", json={"member_count": 42}, headers=TENANT_HEADERS)).json()
    assert updated["member_count"] == 42


async def test_connect_and_disconnect_social_account(client):
    account = (await client.post("/api/v1/marketing/social/accounts", json={"platform": "instagram", "handle": "@voyqi"}, headers=TENANT_HEADERS)).json()
    assert account["connected"] is True
    disconnected = (await client.post(f"/api/v1/marketing/social/accounts/{account['id']}/disconnect", headers=TENANT_HEADERS)).json()
    assert disconnected["connected"] is False


async def test_social_post_requires_approved_draft(client):
    account = (await client.post("/api/v1/marketing/social/accounts", json={"platform": "twitter", "handle": "@voyqi"}, headers=TENANT_HEADERS)).json()
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "Test", "channel": "social"}, headers=TENANT_HEADERS)).json()
    bad_draft = (await client.post("/api/v1/marketing/content", json={"campaign_id": campaign["id"], "channel": "social", "body_text": "guaranteed viral post"}, headers=TENANT_HEADERS)).json()
    resp = await client.post("/api/v1/marketing/social/posts", json={"social_account_id": account["id"], "content_draft_id": bad_draft["id"], "scheduled_at": "2026-08-15T10:00:00Z"}, headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_social_post_schedule_and_publish_populates_calendar(client):
    account = (await client.post("/api/v1/marketing/social/accounts", json={"platform": "linkedin", "handle": "@voyqi"}, headers=TENANT_HEADERS)).json()
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "Test", "channel": "social"}, headers=TENANT_HEADERS)).json()
    draft = (await client.post("/api/v1/marketing/content", json={"campaign_id": campaign["id"], "channel": "social", "body_text": "Check out our new Miami hub."}, headers=TENANT_HEADERS)).json()
    post = (await client.post("/api/v1/marketing/social/posts", json={"social_account_id": account["id"], "content_draft_id": draft["id"], "scheduled_at": "2026-08-17T09:00:00Z"}, headers=TENANT_HEADERS)).json()
    assert post["status"] == "scheduled"

    calendar = (await client.get("/api/v1/marketing/calendar", params={"channel": "social"}, headers=TENANT_HEADERS)).json()
    assert len(calendar) == 1
    assert calendar[0]["reference_type"] == "social_post"

    published = (await client.post(f"/api/v1/marketing/social/posts/{post['id']}/publish", json={"platform_post_id": "li-123"}, headers=TENANT_HEADERS)).json()
    assert published["status"] == "published"
    assert published["platform_post_id"] == "li-123"


async def test_calendar_spans_multiple_channels(client):
    campaign_email = (await client.post("/api/v1/marketing/campaigns", json={"name": "E", "channel": "email"}, headers=TENANT_HEADERS)).json()
    draft_email = (await client.post("/api/v1/marketing/content", json={"campaign_id": campaign_email["id"], "channel": "email", "body_text": "Email body."}, headers=TENANT_HEADERS)).json()
    await client.post("/api/v1/marketing/sends", json={"campaign_id": campaign_email["id"], "content_draft_id": draft_email["id"], "channel": "email", "scheduled_at": "2026-08-15T10:00:00Z"}, headers=TENANT_HEADERS)

    campaign_sms = (await client.post("/api/v1/marketing/campaigns", json={"name": "S", "channel": "sms"}, headers=TENANT_HEADERS)).json()
    draft_sms = (await client.post("/api/v1/marketing/content", json={"campaign_id": campaign_sms["id"], "channel": "sms", "body_text": "SMS body."}, headers=TENANT_HEADERS)).json()
    await client.post("/api/v1/marketing/sends", json={"campaign_id": campaign_sms["id"], "content_draft_id": draft_sms["id"], "channel": "sms", "scheduled_at": "2026-08-16T10:00:00Z"}, headers=TENANT_HEADERS)

    all_entries = (await client.get("/api/v1/marketing/calendar", headers=TENANT_HEADERS)).json()
    channels = {e["channel"] for e in all_entries}
    assert "email" in channels and "sms" in channels


async def test_check_spacing_conflict(client):
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "C", "channel": "email"}, headers=TENANT_HEADERS)).json()
    draft = (await client.post("/api/v1/marketing/content", json={"campaign_id": campaign["id"], "channel": "email", "body_text": "Body."}, headers=TENANT_HEADERS)).json()
    await client.post("/api/v1/marketing/sends", json={"campaign_id": campaign["id"], "content_draft_id": draft["id"], "channel": "email", "scheduled_at": "2026-08-15T10:00:00Z"}, headers=TENANT_HEADERS)

    conflicts = (await client.get("/api/v1/marketing/calendar/check-conflict", params={"channel": "email", "proposed_at": "2026-08-15T14:00:00Z"}, headers=TENANT_HEADERS)).json()
    assert len(conflicts) == 1

    no_conflict = (await client.get("/api/v1/marketing/calendar/check-conflict", params={"channel": "email", "proposed_at": "2026-09-01T10:00:00Z"}, headers=TENANT_HEADERS)).json()
    assert len(no_conflict) == 0
