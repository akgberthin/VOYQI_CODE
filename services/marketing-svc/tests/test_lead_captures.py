import pytest

from tests.conftest import TENANT_HEADERS as H


@pytest.fixture
def published(monkeypatch):
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append((topic, event_type, tenant_id, payload))

    monkeypatch.setattr("src.api.lead_captures.publish_event", _spy)
    return events


async def test_public_capture_publishes_lead_captured(client, published):
    """revenue_agent.handle_lead_captured reads lead_id, name, email, phone."""
    resp = await client.post(
        "/api/v1/marketing/public/lead-captures",
        json={"name": "Ana Lopez", "email": "ana@x.com", "landing_page": "/portable-mobility", "consent_marketing": True},
        headers=H,
    )
    assert resp.status_code == 201
    [(topic, event_type, tenant_id, payload)] = published
    assert (topic, event_type, tenant_id) == ("marketing.leads", "lead.captured", "voyqi-holdings")
    assert payload == {"lead_id": resp.json()["id"], "name": "Ana Lopez", "email": "ana@x.com", "phone": "", "landing_page": "/portable-mobility"}


async def test_capture_needs_email_or_phone(client, published):
    resp = await client.post("/api/v1/marketing/public/lead-captures", json={"name": "Ana"}, headers=H)
    assert resp.status_code == 422
    assert published == []


async def test_capture_against_unknown_campaign_404(client, published):
    resp = await client.post(
        "/api/v1/marketing/public/lead-captures",
        json={"name": "Ana", "phone": "555", "campaign_id": "00000000-0000-0000-0000-000000000000"},
        headers=H,
    )
    assert resp.status_code == 404
    assert published == []


async def test_list_captures_filters_by_campaign(client, published):
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "Q4", "channel": "email", "objective": "acq"}, headers=H)).json()
    await client.post("/api/v1/marketing/public/lead-captures", json={"name": "A", "phone": "1", "campaign_id": campaign["id"]}, headers=H)
    await client.post("/api/v1/marketing/public/lead-captures", json={"name": "B", "phone": "2"}, headers=H)

    all_captures = (await client.get("/api/v1/marketing/lead-captures", headers=H)).json()
    campaign_captures = (await client.get("/api/v1/marketing/lead-captures", params={"campaign_id": campaign["id"]}, headers=H)).json()
    assert len(all_captures) == 2
    assert [c["name"] for c in campaign_captures] == ["A"]
