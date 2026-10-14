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


async def _decision_log(client):
    return (await client.get("/api/v1/ai/decision-log", headers=TENANT_HEADERS)).json()


async def test_disabling_an_agent_is_audited_as_kill_switch(client):
    reg = await client.post("/api/v1/ai/agents", json={"key": "pricing_intelligence_agent", "display_name": "Pricing"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/ai/agents/{reg.json()['id']}/status", json={"status": "disabled"}, headers=TENANT_HEADERS)

    [log] = await _decision_log(client)
    assert log["agent_key"] == "compliance_audit_agent"
    assert log["trigger_event_type"] == "feature_flag.kill_switch_toggled"
    assert log["trigger_context"]["target_agent_key"] == "pricing_intelligence_agent"
    assert log["trigger_context"]["kill_switch_armed"] is True


async def test_compliance_agent_disabling_itself_is_still_audited_and_flagged(client):
    reg = await client.post("/api/v1/ai/agents", json={"key": "compliance_audit_agent", "display_name": "Compliance"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/ai/agents/{reg.json()['id']}/status", json={"status": "disabled"}, headers=TENANT_HEADERS)

    [log] = await _decision_log(client)
    assert log["trigger_context"]["target_agent_key"] == "compliance_audit_agent"
    # 50 (armed) + 30 (disabling itself) — over the default threshold of 50.
    assert log["outcome"] == "deferred"
    recs = (await client.get("/api/v1/ai/recommendations", headers=TENANT_HEADERS)).json()
    assert len(recs) == 1


async def test_status_change_without_on_off_transition_is_not_audited(client):
    reg = await client.post("/api/v1/ai/agents", json={"key": "pricing_intelligence_agent", "display_name": "Pricing"}, headers=TENANT_HEADERS)
    agent_id = reg.json()["id"]
    await client.post(f"/api/v1/ai/agents/{agent_id}/status", json={"status": "paused"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/ai/agents/{agent_id}/status", json={"status": "disabled"}, headers=TENANT_HEADERS)

    assert len(await _decision_log(client)) == 1
