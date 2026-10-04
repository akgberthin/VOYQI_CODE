from tests.conftest import TENANT_HEADERS


async def test_register_and_list_agent(client):
    resp = await client.post(
        "/api/v1/ai/agents",
        json={"key": "compliance_audit_agent", "display_name": "Compliance/Audit Agent"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "active"
    assert resp.json()["human_approval_threshold"] == 50

    resp = await client.get("/api/v1/ai/agents", headers=TENANT_HEADERS)
    assert len(resp.json()) == 1


async def test_pause_agent(client):
    reg = await client.post(
        "/api/v1/ai/agents",
        json={"key": "pricing_intelligence_agent", "display_name": "Pricing Intelligence Agent"},
        headers=TENANT_HEADERS,
    )
    agent_id = reg.json()["id"]

    resp = await client.post(
        f"/api/v1/ai/agents/{agent_id}/status", json={"status": "paused"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


async def test_pause_unknown_agent_404(client):
    import uuid

    resp = await client.post(
        f"/api/v1/ai/agents/{uuid.uuid4()}/status", json={"status": "paused"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 404
