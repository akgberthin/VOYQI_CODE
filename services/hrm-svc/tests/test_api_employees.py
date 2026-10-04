import uuid

from tests.conftest import TENANT_HEADERS


async def test_employee_creation_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import employees
    monkeypatch.setattr(employees, "publish_event", spy)

    resp = await client.post(
        "/api/v1/hrm/employees", json={"full_name": "Jane Doe", "job_title": "Service Advisor", "department": "Dealership", "entity_ref": "rooftop-fl-1"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 201
    assert len(published) == 1
    event_type, payload = published[0]
    assert event_type == "hrm.employee_created"
    assert payload["job_title"] == "Service Advisor"
    assert "checklist_id" in payload


async def test_offboarding_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import employees
    monkeypatch.setattr(employees, "publish_event", spy)

    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    published.clear()

    resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/offboard", headers=TENANT_HEADERS)
    assert resp.json()["employment_status"] == "terminated"
    finalized = [p for e, p in published if e == "hrm.offboarding_initiated"]
    assert len(finalized) == 1


async def test_full_onboarding_checklist_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    employee_id = emp_resp.json()["id"]

    checklists_resp = await client.get(f"/api/v1/hrm/employees/{employee_id}/checklists", headers=TENANT_HEADERS)
    assert len(checklists_resp.json()) == 1
    checklist_id = checklists_resp.json()[0]["id"]
    assert checklists_resp.json()[0]["kind"] == "onboarding"

    task_resp = await client.post(f"/api/v1/hrm/checklists/{checklist_id}/tasks", json={"label": "Provision IT account"}, headers=TENANT_HEADERS)
    assert task_resp.status_code == 201

    blocked_resp = await client.post(f"/api/v1/hrm/checklists/{checklist_id}/mark-complete", headers=TENANT_HEADERS)
    assert blocked_resp.status_code == 409

    await client.post(f"/api/v1/hrm/checklist-tasks/{task_resp.json()['id']}/check", headers=TENANT_HEADERS)
    ready_resp = await client.post(f"/api/v1/hrm/checklists/{checklist_id}/mark-complete", headers=TENANT_HEADERS)
    assert ready_resp.status_code == 200
    assert ready_resp.json()["status"] == "completed"


async def test_position_lifecycle_api(client):
    pos_resp = await client.post(
        "/api/v1/hrm/positions", json={"title": "Service Advisor", "department": "Dealership", "pay_grade": "G4", "salary_band_min": 45000, "salary_band_max": 65000}, headers=TENANT_HEADERS
    )
    assert pos_resp.json()["is_vacant"] is True

    fill_resp = await client.post(f"/api/v1/hrm/positions/{pos_resp.json()['id']}/fill", headers=TENANT_HEADERS)
    assert fill_resp.json()["is_vacant"] is False


async def test_document_skill_cert_contact_compensation_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    employee_id = emp_resp.json()["id"]

    await client.post(f"/api/v1/hrm/employees/{employee_id}/documents", json={"document_type": "contract", "file_ref": "s3://docs/x.pdf"}, headers=TENANT_HEADERS)
    docs = await client.get(f"/api/v1/hrm/employees/{employee_id}/documents", headers=TENANT_HEADERS)
    assert len(docs.json()) == 1

    await client.post(f"/api/v1/hrm/employees/{employee_id}/skills", json={"skill_name": "Diagnostics", "proficiency_level": "expert"}, headers=TENANT_HEADERS)
    skills = await client.get(f"/api/v1/hrm/employees/{employee_id}/skills", headers=TENANT_HEADERS)
    assert len(skills.json()) == 1

    await client.post(f"/api/v1/hrm/employees/{employee_id}/certifications", json={"name": "ASE Master"}, headers=TENANT_HEADERS)
    certs = await client.get(f"/api/v1/hrm/employees/{employee_id}/certifications", headers=TENANT_HEADERS)
    assert len(certs.json()) == 1

    await client.post(f"/api/v1/hrm/employees/{employee_id}/emergency-contacts", json={"name": "John Doe", "relationship": "spouse"}, headers=TENANT_HEADERS)
    contacts = await client.get(f"/api/v1/hrm/employees/{employee_id}/emergency-contacts", headers=TENANT_HEADERS)
    assert len(contacts.json()) == 1

    await client.post(f"/api/v1/hrm/employees/{employee_id}/compensation", json={"annual_salary_usd": 68000, "reason": "merit"}, headers=TENANT_HEADERS)
    comp = await client.get(f"/api/v1/hrm/employees/{employee_id}/compensation", headers=TENANT_HEADERS)
    assert len(comp.json()) == 1


async def test_direct_reports_api(client):
    manager_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Manager Person"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/hrm/employees", json={"full_name": "Report One", "manager_id": manager_resp.json()["id"]}, headers=TENANT_HEADERS)

    reports = await client.get(f"/api/v1/hrm/employees/{manager_resp.json()['id']}/direct-reports", headers=TENANT_HEADERS)
    assert len(reports.json()) == 1


async def test_get_unknown_employee_404(client):
    resp = await client.get(f"/api/v1/hrm/employees/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_populate_checklist_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(
        ai_orchestration_tools, "request_checklist_population",
        lambda tenant_id, checklist_id, kind, job_title, department, entity_ref: {"tasks_added": ["Provision IT account"], "reasoning": "x"},
    )
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe", "department": "dealership"}, headers=TENANT_HEADERS)
    checklists_resp = await client.get(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/checklists", headers=TENANT_HEADERS)
    checklist_id = checklists_resp.json()[0]["id"]

    resp = await client.post(f"/api/v1/hrm/checklists/{checklist_id}/populate-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert "Provision IT account" in resp.json()["tasks_added"]


async def test_populate_checklist_now_unknown_checklist_404(client):
    resp = await client.post(f"/api/v1/hrm/checklists/{uuid.uuid4()}/populate-now", headers=TENANT_HEADERS)
    assert resp.status_code == 404
