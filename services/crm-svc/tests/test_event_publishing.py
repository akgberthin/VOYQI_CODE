import pytest

from tests.conftest import TENANT_HEADERS


@pytest.mark.asyncio
async def test_capture_lead_publishes_lead_captured(client, monkeypatch):
    """lead.captured routes a new lead to the Revenue Agent — its payload
    carries the fields revenue_agent.handle_lead_captured reads."""
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append((topic, event_type, tenant_id, payload))

    monkeypatch.setattr("src.api.crm.publish_event", _spy)
    resp = await client.post(
        "/api/v1/crm/leads",
        json={"first_name": "Jordan", "last_name": "Rivera", "email": "jordan@voyqi.com", "phone": "555-0100", "source": "web"},
        headers=TENANT_HEADERS,
    )

    [(topic, event_type, tenant_id, payload)] = events
    assert topic == "crm.leads"
    assert event_type == "lead.captured"
    assert tenant_id == "voyqi-holdings"
    assert payload == {"lead_id": resp.json()["id"], "name": "Jordan Rivera", "email": "jordan@voyqi.com", "phone": "555-0100", "source": "web"}
