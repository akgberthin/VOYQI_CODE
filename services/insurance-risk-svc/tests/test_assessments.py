import pytest


async def _create_policy(client, tenant_id, coverage, policy_type):
    resp = await client.post(
        "/api/v1/insurance-risk/policies",
        json={"policy_number": "POL-1", "policy_type": policy_type, "insured_name": "Jane Doe", "coverage_amount_usd": coverage},
        headers={"X-Tenant-Id": tenant_id},
    )
    return resp.json()


@pytest.mark.asyncio
async def test_low_coverage_scores_low_band(client, tenant_id):
    policy = await _create_policy(client, tenant_id, coverage=500000.0, policy_type="auto")
    resp = await client.post(
        f"/api/v1/insurance-risk/policies/{policy['id']}/assessments",
        json={"claims_last_12mo": 0},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["risk_score"] == pytest.approx(2.5)
    assert body["risk_band"] == "low"


@pytest.mark.asyncio
async def test_high_coverage_liability_with_claims_scores_high_band(client, tenant_id):
    policy = await _create_policy(client, tenant_id, coverage=10_000_000.0, policy_type="liability")
    resp = await client.post(
        f"/api/v1/insurance-risk/policies/{policy['id']}/assessments",
        json={"claims_last_12mo": 3},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    # coverage component capped at 40, * 1.5 liability multiplier = 60,
    # + claims component capped at 36 = 96
    assert body["risk_score"] == pytest.approx(96.0)
    assert body["risk_band"] == "high"


@pytest.mark.asyncio
async def test_moderate_coverage_with_claims_scores_elevated_band(client, tenant_id):
    policy = await _create_policy(client, tenant_id, coverage=6_000_000.0, policy_type="auto")
    resp = await client.post(
        f"/api/v1/insurance-risk/policies/{policy['id']}/assessments",
        json={"claims_last_12mo": 2},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["risk_score"] == pytest.approx(54.0)
    assert body["risk_band"] == "elevated"


@pytest.mark.asyncio
async def test_unrecognized_policy_type_uses_default_multiplier_and_says_so(client, tenant_id):
    policy = await _create_policy(client, tenant_id, coverage=500000.0, policy_type="marine")
    resp = await client.post(
        f"/api/v1/insurance-risk/policies/{policy['id']}/assessments",
        json={"claims_last_12mo": 0},
        headers={"X-Tenant-Id": tenant_id},
    )
    body = resp.json()
    assert any("unrecognized policy_type" in d for d in body["drivers"])


@pytest.mark.asyncio
async def test_assessment_for_unknown_policy_404s(client, tenant_id):
    resp = await client.post(
        "/api/v1/insurance-risk/policies/00000000-0000-0000-0000-000000000000/assessments",
        json={"claims_last_12mo": 0},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_assessments_returns_history(client, tenant_id):
    policy = await _create_policy(client, tenant_id, coverage=500000.0, policy_type="auto")
    await client.post(
        f"/api/v1/insurance-risk/policies/{policy['id']}/assessments", json={"claims_last_12mo": 0}, headers={"X-Tenant-Id": tenant_id}
    )
    await client.post(
        f"/api/v1/insurance-risk/policies/{policy['id']}/assessments", json={"claims_last_12mo": 1}, headers={"X-Tenant-Id": tenant_id}
    )
    resp = await client.get(f"/api/v1/insurance-risk/policies/{policy['id']}/assessments", headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 200
    assert len(resp.json()) == 2
