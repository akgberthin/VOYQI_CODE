from tests.conftest import TENANT_HEADERS


async def _create_account(client, seat_limit=2):
    resp = await client.post("/api/v1/subscription/corporate/accounts", json={
        "company_name": "Acme Logistics", "billing_contact_email": "billing@acme.com", "seat_limit": seat_limit,
    }, headers=TENANT_HEADERS)
    return resp.json()


async def test_create_and_list_corporate_account(client):
    account = await _create_account(client)
    assert account["status"] == "active"
    listed = (await client.get("/api/v1/subscription/corporate/accounts", headers=TENANT_HEADERS)).json()
    assert any(a["id"] == account["id"] for a in listed)


async def test_add_employee_within_seat_limit(client):
    account = await _create_account(client, seat_limit=2)
    resp = await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-1", "full_name": "Jane Employee", "email": "jane@acme.com",
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    employee = resp.json()
    assert employee["member_type"] == "corporate"
    assert employee["organization_id"] == account["id"]


async def test_add_employee_beyond_seat_limit_refused(client):
    """The hard business rule: refused outright, not silently
    allowed and reconciled later."""
    account = await _create_account(client, seat_limit=1)
    await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-1", "full_name": "First Employee", "email": "first@acme.com",
    }, headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-2", "full_name": "Second Employee", "email": "second@acme.com",
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_remove_employee_frees_seat_and_reverts_to_personal(client):
    account = await _create_account(client, seat_limit=1)
    employee = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-1", "full_name": "Jane Employee", "email": "jane@acme.com",
    }, headers=TENANT_HEADERS)).json()

    removed = (await client.post(f"/api/v1/subscription/corporate/employees/{employee['id']}/remove", headers=TENANT_HEADERS)).json()
    assert removed["organization_id"] is None
    assert removed["member_type"] == "personal"

    # Seat is now free — adding a new employee at the same limit succeeds.
    resp = await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-2", "full_name": "New Employee", "email": "new@acme.com",
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 201


async def test_billing_summary_aggregates_across_employees(client):
    account = await _create_account(client, seat_limit=5)
    await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-1", "full_name": "Employee One", "email": "one@acme.com",
    }, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-2", "full_name": "Employee Two", "email": "two@acme.com",
    }, headers=TENANT_HEADERS)

    summary = (await client.get(f"/api/v1/subscription/corporate/accounts/{account['id']}/billing-summary", headers=TENANT_HEADERS)).json()
    assert summary["seats_used"] == 2
    assert summary["seats_available"] == 3


async def test_evaluate_utilization_flags_underutilizing_employee(client):
    account = await _create_account(client, seat_limit=5)
    employee = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-1", "full_name": "Rarely Drives", "email": "rare@acme.com",
    }, headers=TENANT_HEADERS)).json()

    flags = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/evaluate-utilization", json={
        "trips_by_member": {employee["id"]: 1},
    }, headers=TENANT_HEADERS)).json()
    assert len(flags) == 1
    assert flags[0]["flag_type"] == "underutilizing"
    assert flags[0]["resolved"] is False


async def test_evaluate_utilization_flags_overutilizing_employee(client):
    account = await _create_account(client, seat_limit=5)
    employee = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-1", "full_name": "Always Driving", "email": "always@acme.com",
    }, headers=TENANT_HEADERS)).json()

    flags = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/evaluate-utilization", json={
        "trips_by_member": {employee["id"]: 25},
    }, headers=TENANT_HEADERS)).json()
    assert flags[0]["flag_type"] == "overutilizing"


async def test_evaluate_utilization_does_not_flag_normal_usage(client):
    account = await _create_account(client, seat_limit=5)
    employee = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-1", "full_name": "Normal User", "email": "normal@acme.com",
    }, headers=TENANT_HEADERS)).json()

    flags = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/evaluate-utilization", json={
        "trips_by_member": {employee["id"]: 8},
    }, headers=TENANT_HEADERS)).json()
    assert flags == []


async def test_resolve_utilization_flag(client):
    account = await _create_account(client, seat_limit=5)
    employee = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/employees", json={
        "user_id": "u-emp-1", "full_name": "Rarely Drives", "email": "rare@acme.com",
    }, headers=TENANT_HEADERS)).json()
    flags = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/evaluate-utilization", json={
        "trips_by_member": {employee["id"]: 0},
    }, headers=TENANT_HEADERS)).json()

    resolved = (await client.post(f"/api/v1/subscription/corporate/utilization-flags/{flags[0]['id']}/resolve", json={
        "resolved_by": "corporate-admin-1",
    }, headers=TENANT_HEADERS)).json()
    assert resolved["resolved"] is True
    assert resolved["resolved_by"] == "corporate-admin-1"

    unresolved = (await client.get(f"/api/v1/subscription/corporate/accounts/{account['id']}/utilization-flags", headers=TENANT_HEADERS)).json()
    assert unresolved == []


async def test_suspend_and_reactivate_corporate_account(client):
    account = await _create_account(client)
    suspended = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/suspend", headers=TENANT_HEADERS)).json()
    assert suspended["status"] == "suspended"
    reactivated = (await client.post(f"/api/v1/subscription/corporate/accounts/{account['id']}/reactivate", headers=TENANT_HEADERS)).json()
    assert reactivated["status"] == "active"


async def test_add_employee_to_nonexistent_account_404(client):
    import uuid
    resp = await client.post(f"/api/v1/subscription/corporate/accounts/{uuid.uuid4()}/employees", json={
        "user_id": "u-1", "full_name": "Test", "email": "test@test.com",
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 404
