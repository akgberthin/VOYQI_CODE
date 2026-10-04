import pytest


async def _create_policy(client, tenant_id, key="abac-sensitive-data"):
    resp = await client.post(
        "/api/v1/security-compliance/policies",
        json={"key": key, "name": "Sensitive Data Access", "description": "Grants read access to PII fields"},
        headers={"X-Tenant-Id": tenant_id},
    )
    return resp.json()


async def _request_grant(client, tenant_id, policy_id, subject_id="user-1"):
    resp = await client.post(
        "/api/v1/security-compliance/grants",
        json={"policy_id": policy_id, "subject_type": "user", "subject_id": subject_id},
        headers={"X-Tenant-Id": tenant_id},
    )
    return resp.json()


@pytest.mark.asyncio
async def test_request_grant_starts_pending_and_unsimulated(client, tenant_id):
    policy = await _create_policy(client, tenant_id)
    grant = await _request_grant(client, tenant_id, policy["id"])
    assert grant["status"] == "pending"
    assert grant["simulated"] is False
    assert grant["simulation_result"] is None


@pytest.mark.asyncio
async def test_request_grant_for_unknown_policy_404s(client, tenant_id):
    resp = await client.post(
        "/api/v1/security-compliance/grants",
        json={"policy_id": "00000000-0000-0000-0000-000000000000", "subject_type": "user", "subject_id": "user-1"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_apply_without_simulation_is_hard_blocked(client, tenant_id):
    """No risk score, no threshold, no path around this — a grant that
    has never been simulated cannot be applied, even with an
    accountable_role supplied."""
    policy = await _create_policy(client, tenant_id)
    grant = await _request_grant(client, tenant_id, policy["id"])

    resp = await client.post(
        f"/api/v1/security-compliance/grants/{grant['id']}/apply",
        json={"accountable_role": "security_lead"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 400
    assert "simulation" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_apply_after_simulation_without_accountable_role_is_hard_blocked(client, tenant_id):
    policy = await _create_policy(client, tenant_id)
    grant = await _request_grant(client, tenant_id, policy["id"])
    await client.post(f"/api/v1/security-compliance/grants/{grant['id']}/simulate", headers={"X-Tenant-Id": tenant_id})

    resp = await client.post(
        f"/api/v1/security-compliance/grants/{grant['id']}/apply", json={}, headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 400
    assert "accountable_role" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_simulate_then_apply_with_accountable_role_succeeds(client, tenant_id):
    policy = await _create_policy(client, tenant_id)
    grant = await _request_grant(client, tenant_id, policy["id"])

    sim_resp = await client.post(f"/api/v1/security-compliance/grants/{grant['id']}/simulate", headers={"X-Tenant-Id": tenant_id})
    assert sim_resp.status_code == 200
    sim_body = sim_resp.json()
    assert sim_body["simulated"] is True
    assert sim_body["simulation_result"]["safe_to_apply"] is True

    resp = await client.post(
        f"/api/v1/security-compliance/grants/{grant['id']}/apply",
        json={"accountable_role": "security_lead"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["accountable_role"] == "security_lead"


@pytest.mark.asyncio
async def test_simulation_flags_conflicting_pending_request(client, tenant_id):
    policy = await _create_policy(client, tenant_id)
    await _request_grant(client, tenant_id, policy["id"], subject_id="user-1")
    grant2 = await _request_grant(client, tenant_id, policy["id"], subject_id="user-1")

    sim_resp = await client.post(f"/api/v1/security-compliance/grants/{grant2['id']}/simulate", headers={"X-Tenant-Id": tenant_id})
    sim_body = sim_resp.json()
    assert len(sim_body["simulation_result"]["conflicting_request_ids"]) == 1
    assert sim_body["simulation_result"]["safe_to_apply"] is False


@pytest.mark.asyncio
async def test_cannot_apply_an_already_decided_grant(client, tenant_id):
    policy = await _create_policy(client, tenant_id)
    grant = await _request_grant(client, tenant_id, policy["id"])
    await client.post(f"/api/v1/security-compliance/grants/{grant['id']}/simulate", headers={"X-Tenant-Id": tenant_id})
    await client.post(
        f"/api/v1/security-compliance/grants/{grant['id']}/apply",
        json={"accountable_role": "security_lead"},
        headers={"X-Tenant-Id": tenant_id},
    )

    resp = await client.post(
        f"/api/v1/security-compliance/grants/{grant['id']}/apply",
        json={"accountable_role": "security_lead"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_reject_grant(client, tenant_id):
    policy = await _create_policy(client, tenant_id)
    grant = await _request_grant(client, tenant_id, policy["id"])

    resp = await client.post(f"/api/v1/security-compliance/grants/{grant['id']}/reject", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_list_grants_filtered_by_status(client, tenant_id):
    policy = await _create_policy(client, tenant_id)
    await _request_grant(client, tenant_id, policy["id"])

    resp = await client.get("/api/v1/security-compliance/grants", params={"status": "approved"}, headers={"X-Tenant-Id": tenant_id})
    assert resp.json() == []

    resp = await client.get("/api/v1/security-compliance/grants", params={"status": "pending"}, headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1
