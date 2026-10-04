import uuid

from tests.conftest import TENANT_HEADERS


async def test_request_attrition_risk_assessment_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import workforce_analytics
    monkeypatch.setattr(workforce_analytics, "publish_event", spy)

    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe", "job_title": "Service Advisor", "department": "Service", "annual_salary_usd": 55000}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/request-attrition-risk-assessment", headers=TENANT_HEADERS)
    assert resp.status_code == 202
    event_type, payload = published[0]
    assert event_type == "hrm.attrition_risk_requested"
    assert payload["employee_id"] == emp_resp.json()["id"]


async def test_request_attrition_risk_assessment_unknown_employee_404(client):
    resp = await client.post(f"/api/v1/hrm/employees/{uuid.uuid4()}/request-attrition-risk-assessment", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_assess_attrition_risk_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(
        ai_orchestration_tools, "request_attrition_risk",
        lambda tenant_id, employee_id, factors: {"risk_score": 0.4, "reasoning": "x"},
    )
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/assess-attrition-risk-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["risk_score"] == 0.4


async def test_full_attrition_risk_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    employee_id = emp_resp.json()["id"]

    score_resp = await client.post(f"/api/v1/hrm/employees/{employee_id}/attrition-risk-scores", params={"risk_score": 0.65, "reasoning": "compensation stagnation"}, headers=TENANT_HEADERS)
    assert score_resp.status_code == 201

    latest_resp = await client.get(f"/api/v1/hrm/employees/{employee_id}/attrition-risk-scores/latest", headers=TENANT_HEADERS)
    assert latest_resp.json()["risk_score"] == 0.65


async def test_attrition_risk_watchlist_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/attrition-risk-scores", params={"risk_score": 0.75, "reasoning": "high risk"}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/hrm/attrition-risk-scores/watchlist", params={"threshold": 0.5}, headers=TENANT_HEADERS)
    assert len(resp.json()) >= 1
    assert all(s["risk_score"] >= 0.5 for s in resp.json())


async def test_headcount_by_department_api(client):
    await client.post("/api/v1/hrm/employees", json={"full_name": "A", "department": "Sales"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/hrm/employees", json={"full_name": "B", "department": "Sales"}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/hrm/analytics/headcount-by-department", headers=TENANT_HEADERS)
    sales = next(h for h in resp.json() if h["department"] == "Sales")
    assert sales["headcount"] == 2


async def test_compensation_benchmark_api(client):
    emp_resp = await client.post(
        "/api/v1/hrm/employees", json={"full_name": "Jane Doe", "job_title": "Service Advisor", "department": "Service", "annual_salary_usd": 55000}, headers=TENANT_HEADERS
    )
    await client.post("/api/v1/hrm/positions", json={"title": "Service Advisor", "department": "Service", "salary_band_min": 45000, "salary_band_max": 65000}, headers=TENANT_HEADERS)

    resp = await client.get(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/compensation-benchmark", headers=TENANT_HEADERS)
    assert resp.json()["band_position_pct"] == 50.0


async def test_pay_equity_snapshot_api(client):
    await client.post("/api/v1/hrm/employees", json={"full_name": "A", "department": "Sales", "annual_salary_usd": 60000}, headers=TENANT_HEADERS)
    await client.post("/api/v1/hrm/employees", json={"full_name": "B", "department": "Sales", "annual_salary_usd": 70000}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/hrm/analytics/pay-equity-snapshot", headers=TENANT_HEADERS)
    sales_group = next(s for s in resp.json() if s["department"] == "Sales")
    assert sales_group["employee_count"] == 2
    assert sales_group["avg_salary_usd"] == 65000.0
