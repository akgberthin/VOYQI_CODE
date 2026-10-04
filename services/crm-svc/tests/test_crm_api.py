import uuid

import pytest

from tests.conftest import TENANT_HEADERS


@pytest.mark.asyncio
async def test_capture_lead_via_api(client):
    resp = await client.post("/api/v1/crm/leads", json={"first_name": "Jordan", "last_name": "Rivera", "email": "jordan@voyqi.com", "source": "web"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["status"] == "new"


@pytest.mark.asyncio
async def test_qualify_and_convert_lead_full_flow(client):
    lead_resp = await client.post("/api/v1/crm/leads", json={"first_name": "Jordan", "last_name": "Rivera", "email": "jordan2@voyqi.com"}, headers=TENANT_HEADERS)
    lead_id = lead_resp.json()["id"]

    qualify_resp = await client.post(f"/api/v1/crm/leads/{lead_id}/qualify", json={"qualified": True}, headers=TENANT_HEADERS)
    assert qualify_resp.status_code == 200
    assert qualify_resp.json()["status"] == "qualified"

    convert_resp = await client.post(
        f"/api/v1/crm/leads/{lead_id}/convert",
        json={"deal_name": "Rivera deal", "value_cents": 85900, "owner_id": str(uuid.uuid4()), "mobility_class": "executive"},
        headers=TENANT_HEADERS,
    )
    assert convert_resp.status_code == 201
    assert convert_resp.json()["stage"] == "prospecting"


@pytest.mark.asyncio
async def test_deal_stage_progression_via_api(client):
    deal_resp = await client.post("/api/v1/crm/deals", json={"name": "Direct deal", "value_cents": 50000, "owner_id": str(uuid.uuid4())}, headers=TENANT_HEADERS)
    deal_id = deal_resp.json()["id"]

    advance_resp = await client.post(f"/api/v1/crm/deals/{deal_id}/advance", json={"new_stage": "proposal"}, headers=TENANT_HEADERS)
    assert advance_resp.status_code == 200
    assert advance_resp.json()["stage"] == "proposal"

    close_resp = await client.post(f"/api/v1/crm/deals/{deal_id}/close", json={"won": True}, headers=TENANT_HEADERS)
    assert close_resp.status_code == 200
    assert close_resp.json()["stage"] == "closed_won"

    reclose_resp = await client.post(f"/api/v1/crm/deals/{deal_id}/close", json={"won": False}, headers=TENANT_HEADERS)
    assert reclose_resp.status_code == 409


@pytest.mark.asyncio
async def test_invalid_stage_transition_returns_422(client):
    deal_resp = await client.post("/api/v1/crm/deals", json={"name": "Deal", "value_cents": 50000, "owner_id": str(uuid.uuid4())}, headers=TENANT_HEADERS)
    deal_id = deal_resp.json()["id"]

    resp = await client.post(f"/api/v1/crm/deals/{deal_id}/advance", json={"new_stage": "made_up_stage"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_log_activity_via_api(client):
    deal_resp = await client.post("/api/v1/crm/deals", json={"name": "Deal", "value_cents": 50000, "owner_id": str(uuid.uuid4())}, headers=TENANT_HEADERS)
    deal_id = deal_resp.json()["id"]

    resp = await client.post(
        "/api/v1/crm/activities",
        json={"activity_type": "call", "subject": "Discovery call", "created_by": str(uuid.uuid4()), "deal_id": deal_id, "notes": "Positive signal"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_log_activity_without_deal_or_lead_returns_422(client):
    resp = await client.post("/api/v1/crm/activities", json={"activity_type": "note", "subject": "General note", "created_by": str(uuid.uuid4())}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pipeline_summary_via_api(client):
    owner_id = str(uuid.uuid4())
    await client.post("/api/v1/crm/deals", json={"name": "D1", "value_cents": 10000, "owner_id": owner_id}, headers=TENANT_HEADERS)
    await client.post("/api/v1/crm/deals", json={"name": "D2", "value_cents": 20000, "owner_id": owner_id}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/crm/pipeline/summary", params={"owner_id": owner_id}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["prospecting"]["count"] == 2
    assert resp.json()["prospecting"]["total_value_cents"] == 30000


@pytest.mark.asyncio
async def test_get_lead_404_for_unknown_id(client):
    resp = await client.get(f"/api/v1/crm/leads/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404
