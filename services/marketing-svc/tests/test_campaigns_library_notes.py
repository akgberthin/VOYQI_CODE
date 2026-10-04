from tests.conftest import TENANT_HEADERS


async def test_create_and_list_campaign(client):
    resp = await client.post("/api/v1/marketing/campaigns", json={"name": "Q3 Push", "channel": "sms", "objective": "reactivation"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["status"] == "draft"
    listed = (await client.get("/api/v1/marketing/campaigns", headers=TENANT_HEADERS)).json()
    assert any(c["name"] == "Q3 Push" for c in listed)


async def test_filter_campaigns_by_channel(client):
    await client.post("/api/v1/marketing/campaigns", json={"name": "Email 1", "channel": "email"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/marketing/campaigns", json={"name": "Voice 1", "channel": "voice"}, headers=TENANT_HEADERS)
    voice_only = (await client.get("/api/v1/marketing/campaigns", params={"channel": "voice"}, headers=TENANT_HEADERS)).json()
    assert all(c["channel"] == "voice" for c in voice_only)


async def test_set_campaign_status(client):
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "Test", "channel": "email"}, headers=TENANT_HEADERS)).json()
    updated = (await client.patch(f"/api/v1/marketing/campaigns/{campaign['id']}/status", json={"status": "active"}, headers=TENANT_HEADERS)).json()
    assert updated["status"] == "active"


async def test_create_and_list_library_item(client):
    resp = await client.post("/api/v1/marketing/library", json={"title": "Welcome email template", "channel": "email", "body_text": "Welcome to VOYQI.", "tags": ["onboarding"]}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    listed = (await client.get("/api/v1/marketing/library", headers=TENANT_HEADERS)).json()
    assert any(i["title"] == "Welcome email template" for i in listed)


async def test_delete_library_item(client):
    item = (await client.post("/api/v1/marketing/library", json={"title": "Temp", "channel": "sms", "body_text": "..."}, headers=TENANT_HEADERS)).json()
    resp = await client.delete(f"/api/v1/marketing/library/{item['id']}", headers=TENANT_HEADERS)
    assert resp.status_code == 200


async def test_create_and_list_notes(client):
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "Test", "channel": "email"}, headers=TENANT_HEADERS)).json()
    resp = await client.post("/api/v1/marketing/notes", json={"author": "jane", "body": "Waiting on legal review of the offer terms.", "campaign_id": campaign["id"]}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    listed = (await client.get("/api/v1/marketing/notes", params={"campaign_id": campaign["id"]}, headers=TENANT_HEADERS)).json()
    assert len(listed) == 1


async def test_delete_note(client):
    note = (await client.post("/api/v1/marketing/notes", json={"author": "jane", "body": "temp note"}, headers=TENANT_HEADERS)).json()
    resp = await client.delete(f"/api/v1/marketing/notes/{note['id']}", headers=TENANT_HEADERS)
    assert resp.status_code == 200
