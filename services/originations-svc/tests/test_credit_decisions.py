import pytest


async def _create_application(client, tenant_id):
    resp = await client.post(
        "/api/v1/originations/applications",
        json={"applicant_name": "Jane Doe", "requested_amount_usd": 40000.0, "annual_income_usd": 60000.0, "fico_score": 700},
        headers={"X-Tenant-Id": tenant_id},
    )
    return resp.json()


async def _request_decision(client, tenant_id, application_id):
    resp = await client.post(
        "/api/v1/originations/credit-decisions",
        json={"application_id": application_id},
        headers={"X-Tenant-Id": tenant_id},
    )
    return resp.json()


@pytest.mark.asyncio
async def test_request_decision_starts_pending_and_moves_application(client, tenant_id):
    application = await _create_application(client, tenant_id)
    decision = await _request_decision(client, tenant_id, application["id"])
    assert decision["status"] == "pending"

    app_resp = await client.get(f"/api/v1/originations/applications/{application['id']}", headers={"X-Tenant-Id": tenant_id})
    assert app_resp.json()["status"] == "pending_decision"


@pytest.mark.asyncio
async def test_request_decision_for_unknown_application_404s(client, tenant_id):
    resp = await client.post(
        "/api/v1/originations/credit-decisions",
        json={"application_id": "00000000-0000-0000-0000-000000000000"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_approve_without_accountable_role_is_hard_blocked(client, tenant_id):
    application = await _create_application(client, tenant_id)
    decision = await _request_decision(client, tenant_id, application["id"])

    resp = await client.post(
        f"/api/v1/originations/credit-decisions/{decision['id']}/decide",
        json={"approve": True},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 400
    assert "accountable_role" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_decline_without_accountable_role_is_also_hard_blocked(client, tenant_id):
    """An adverse credit action needs accountable documentation under
    fair-lending law just as much as an approval does — the gate
    doesn't only apply to the favorable outcome."""
    application = await _create_application(client, tenant_id)
    decision = await _request_decision(client, tenant_id, application["id"])

    resp = await client.post(
        f"/api/v1/originations/credit-decisions/{decision['id']}/decide",
        json={"approve": False},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 400
    assert "accountable_role" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_approve_with_accountable_role_succeeds_and_updates_application(client, tenant_id):
    application = await _create_application(client, tenant_id)
    decision = await _request_decision(client, tenant_id, application["id"])

    resp = await client.post(
        f"/api/v1/originations/credit-decisions/{decision['id']}/decide",
        json={"approve": True, "accountable_role": "senior_underwriter", "reasoning": "income verified"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["accountable_role"] == "senior_underwriter"

    app_resp = await client.get(f"/api/v1/originations/applications/{application['id']}", headers={"X-Tenant-Id": tenant_id})
    assert app_resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_decline_with_accountable_role_succeeds(client, tenant_id):
    application = await _create_application(client, tenant_id)
    decision = await _request_decision(client, tenant_id, application["id"])

    resp = await client.post(
        f"/api/v1/originations/credit-decisions/{decision['id']}/decide",
        json={"approve": False, "accountable_role": "senior_underwriter", "reasoning": "insufficient income"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "declined"

    app_resp = await client.get(f"/api/v1/originations/applications/{application['id']}", headers={"X-Tenant-Id": tenant_id})
    assert app_resp.json()["status"] == "declined"


@pytest.mark.asyncio
async def test_cannot_decide_twice(client, tenant_id):
    application = await _create_application(client, tenant_id)
    decision = await _request_decision(client, tenant_id, application["id"])
    await client.post(
        f"/api/v1/originations/credit-decisions/{decision['id']}/decide",
        json={"approve": True, "accountable_role": "senior_underwriter"},
        headers={"X-Tenant-Id": tenant_id},
    )

    resp = await client.post(
        f"/api/v1/originations/credit-decisions/{decision['id']}/decide",
        json={"approve": True, "accountable_role": "senior_underwriter"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_decide_unknown_decision_404s(client, tenant_id):
    resp = await client.post(
        "/api/v1/originations/credit-decisions/00000000-0000-0000-0000-000000000000/decide",
        json={"approve": True, "accountable_role": "senior_underwriter"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_decisions_filtered_by_status(client, tenant_id):
    application = await _create_application(client, tenant_id)
    await _request_decision(client, tenant_id, application["id"])

    resp = await client.get("/api/v1/originations/credit-decisions", params={"status": "approved"}, headers={"X-Tenant-Id": tenant_id})
    assert resp.json() == []

    resp = await client.get("/api/v1/originations/credit-decisions", params={"status": "pending"}, headers={"X-Tenant-Id": tenant_id})
    assert len(resp.json()) == 1
