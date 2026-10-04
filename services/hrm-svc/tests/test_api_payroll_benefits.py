
from tests.conftest import TENANT_HEADERS


async def test_full_pay_run_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    employee_id = emp_resp.json()["id"]
    await client.post(f"/api/v1/hrm/employees/{employee_id}/tax-status", json={"work_state": "FL", "residence_state": "FL"}, headers=TENANT_HEADERS)

    run_resp = await client.post(
        "/api/v1/hrm/pay-runs", json={"entity_ref": "rooftop-fl-1", "pay_period_start": "2026-07-01T00:00:00Z", "pay_period_end": "2026-07-14T00:00:00Z"}, headers=TENANT_HEADERS
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]

    stub_resp = await client.post(f"/api/v1/hrm/pay-runs/{run_id}/pay-stubs", json={"employee_id": employee_id, "gross_pay_usd": 2500.0}, headers=TENANT_HEADERS)
    assert stub_resp.json()["state_tax_usd"] == 0.0  # FL has no state income tax

    process_resp = await client.post(f"/api/v1/hrm/pay-runs/{run_id}/process", headers=TENANT_HEADERS)
    assert process_resp.json()["status"] == "completed"
    assert process_resp.json()["total_gross_usd"] == 2500.0


async def test_garnishment_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    employee_id = emp_resp.json()["id"]

    order_resp = await client.post(f"/api/v1/hrm/employees/{employee_id}/garnishments", json={"order_type": "child_support", "amount_per_period_usd": 200.0}, headers=TENANT_HEADERS)
    assert order_resp.status_code == 201

    deactivate_resp = await client.post(f"/api/v1/hrm/garnishments/{order_resp.json()['id']}/deactivate", headers=TENANT_HEADERS)
    assert deactivate_resp.json()["is_active"] is False


async def test_benefits_recommendation_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import benefits
    monkeypatch.setattr(benefits, "publish_event", spy)

    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe", "department": "dealership"}, headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/request-benefits-recommendation", headers=TENANT_HEADERS)
    assert resp.status_code == 202
    event_type, payload = published[0]
    assert event_type == "hrm.benefits_recommendation_requested"


async def test_full_benefits_recommendation_accept_flow_api(client):
    plan_resp = await client.post(
        "/api/v1/hrm/benefits-plans", json={"name": "VOYQI PPO Gold", "plan_type": "medical", "tier": "employee_only", "monthly_premium_usd": 450.0, "employer_contribution_pct": 0.7}, headers=TENANT_HEADERS
    )
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    employee_id = emp_resp.json()["id"]

    await client.post(f"/api/v1/hrm/employees/{employee_id}/benefits-recommendations", json={"recommended_plan_id": plan_resp.json()["id"], "reasoning": "best fit"}, headers=TENANT_HEADERS)

    pending_resp = await client.get(f"/api/v1/hrm/employees/{employee_id}/benefits-recommendations/pending", headers=TENANT_HEADERS)
    assert len(pending_resp.json()) == 1

    accept_resp = await client.post(f"/api/v1/hrm/benefits-recommendations/{pending_resp.json()[0]['id']}/accept", headers=TENANT_HEADERS)
    assert accept_resp.status_code == 200
    assert accept_resp.json()["plan_id"] == plan_resp.json()["id"]

    enrollments_resp = await client.get(f"/api/v1/hrm/employees/{employee_id}/benefits-enrollments", headers=TENANT_HEADERS)
    assert len(enrollments_resp.json()) == 1


async def test_benefits_recommendation_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(
        ai_orchestration_tools, "request_benefits_recommendation",
        lambda tenant_id, employee_id, job_title, department: {"recommended_plan_id": None, "reasoning": "x"},
    )
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/benefits-recommendation-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200


async def test_terminate_enrollment_api(client):
    plan_resp = await client.post(
        "/api/v1/hrm/benefits-plans", json={"name": "VOYQI Dental", "plan_type": "dental", "tier": "employee_only", "monthly_premium_usd": 30.0}, headers=TENANT_HEADERS
    )
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    enroll_resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/benefits-enrollments", json={"plan_id": plan_resp.json()["id"]}, headers=TENANT_HEADERS)

    terminate_resp = await client.post(f"/api/v1/hrm/benefits-enrollments/{enroll_resp.json()['id']}/terminate", headers=TENANT_HEADERS)
    assert terminate_resp.json()["status"] == "terminated"
