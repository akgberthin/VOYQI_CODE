from tests.conftest import TENANT_HEADERS


async def test_create_and_list_goal(client):
    resp = await client.post(
        "/api/v1/ai/goals",
        json={"agent_key": "compliance_audit_agent", "goal_key": "risk_threshold", "target_value": 50, "unit": "score"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["target_value"] == 50

    resp = await client.get("/api/v1/ai/goals", headers=TENANT_HEADERS)
    assert len(resp.json()) == 1


async def test_update_goal_recalibrates_immediately(client):
    create_resp = await client.post(
        "/api/v1/ai/goals",
        json={"agent_key": "compliance_audit_agent", "goal_key": "risk_threshold", "target_value": 50},
        headers=TENANT_HEADERS,
    )
    goal_id = create_resp.json()["id"]

    update_resp = await client.put(
        f"/api/v1/ai/goals/{goal_id}", json={"target_value": 20}, headers=TENANT_HEADERS
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["target_value"] == 20


async def test_update_unknown_goal_404(client):
    import uuid

    resp = await client.put(
        f"/api/v1/ai/goals/{uuid.uuid4()}", json={"target_value": 10}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 404
