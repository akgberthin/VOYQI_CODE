from tests.conftest import TENANT_HEADERS


async def test_create_draft_policy(client):
    resp = await client.post(
        "/api/v1/ai/policies",
        json={"agent_key": "compliance_audit_agent", "system_prompt": "You are the Compliance Agent v1."},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["version"] == 1
    assert resp.json()["status"] == "draft"


async def test_version_increments_per_agent(client):
    await client.post(
        "/api/v1/ai/policies",
        json={"agent_key": "compliance_audit_agent", "system_prompt": "v1"},
        headers=TENANT_HEADERS,
    )
    resp = await client.post(
        "/api/v1/ai/policies",
        json={"agent_key": "compliance_audit_agent", "system_prompt": "v2"},
        headers=TENANT_HEADERS,
    )
    assert resp.json()["version"] == 2


async def test_promote_requires_passed_eval(client):
    create_resp = await client.post(
        "/api/v1/ai/policies",
        json={"agent_key": "compliance_audit_agent", "system_prompt": "v1"},
        headers=TENANT_HEADERS,
    )
    policy_id = create_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/ai/policies/{policy_id}/promote",
        json={"eval_suite_passed": False},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422


async def test_promote_retires_prior_active_version(client):
    v1 = await client.post(
        "/api/v1/ai/policies",
        json={"agent_key": "compliance_audit_agent", "system_prompt": "v1"},
        headers=TENANT_HEADERS,
    )
    await client.post(
        f"/api/v1/ai/policies/{v1.json()['id']}/promote",
        json={"eval_suite_passed": True, "eval_summary": "100% pass"},
        headers=TENANT_HEADERS,
    )

    v2 = await client.post(
        "/api/v1/ai/policies",
        json={"agent_key": "compliance_audit_agent", "system_prompt": "v2"},
        headers=TENANT_HEADERS,
    )
    await client.post(
        f"/api/v1/ai/policies/{v2.json()['id']}/promote",
        json={"eval_suite_passed": True, "eval_summary": "100% pass"},
        headers=TENANT_HEADERS,
    )

    policies_resp = await client.get(
        "/api/v1/ai/policies", params={"agent_key": "compliance_audit_agent"}, headers=TENANT_HEADERS
    )
    statuses = {p["version"]: p["status"] for p in policies_resp.json()}
    assert statuses[1] == "retired"
    assert statuses[2] == "active"
