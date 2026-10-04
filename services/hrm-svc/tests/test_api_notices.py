import uuid

from tests.conftest import TENANT_HEADERS


async def test_create_and_list_notices_api(client):
    resp = await client.post("/api/v1/hrm/notices", json={"title": "Q3 Travel Policy Update", "body": "Pre-approval now required.", "posted_by": "hr-ops-1", "channels": "app,email"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201

    list_resp = await client.get("/api/v1/hrm/notices", headers=TENANT_HEADERS)
    assert len(list_resp.json()) == 1


async def test_notice_comment_flow_api(client):
    notice_resp = await client.post("/api/v1/hrm/notices", json={"title": "New hub", "body": "Austin hub opens next month."}, headers=TENANT_HEADERS)

    comment_resp = await client.post(
        f"/api/v1/hrm/notices/{notice_resp.json()['id']}/comments", json={"employee_id": str(uuid.uuid4()), "body": "Excited!"}, headers=TENANT_HEADERS
    )
    assert comment_resp.status_code == 201

    list_resp = await client.get(f"/api/v1/hrm/notices/{notice_resp.json()['id']}/comments", headers=TENANT_HEADERS)
    assert len(list_resp.json()) == 1


async def test_comment_on_unknown_notice_404(client):
    resp = await client.post("/api/v1/hrm/notices/" + str(uuid.uuid4()) + "/comments", json={"employee_id": str(uuid.uuid4()), "body": "x"}, headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_add_and_list_notes_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)

    note_resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/notes", json={"body": "Discussed career goals.", "author": "manager-jane"}, headers=TENANT_HEADERS)
    assert note_resp.status_code == 201
    assert note_resp.json()["is_ai_generated"] is False

    list_resp = await client.get(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/notes", headers=TENANT_HEADERS)
    assert len(list_resp.json()) == 1


async def test_request_note_update_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import notices
    monkeypatch.setattr(notices, "publish_event", spy)

    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/request-note-update", params={"context": "Completed onboarding"}, headers=TENANT_HEADERS)
    assert resp.status_code == 202
    event_type, payload = published[0]
    assert event_type == "hrm.note_update_requested"
    assert payload["context"] == "Completed onboarding"


async def test_request_note_update_unknown_employee_404(client):
    resp = await client.post(f"/api/v1/hrm/employees/{uuid.uuid4()}/request-note-update", params={"context": "x"}, headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_update_note_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_note_update", lambda tenant_id, employee_id, context: {"note_written": True, "reasoning": "x"})
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/update-note-now", params={"context": "Completed onboarding"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["note_written"] is True


async def test_case_resolution_triggers_note_update_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import case_management
    monkeypatch.setattr(case_management, "publish_event", spy)

    emp_id = str(uuid.uuid4())
    case_resp = await client.post("/api/v1/hrm/cases", json={"employee_id": emp_id, "case_type": "policy_question", "subject": "PTO carryover"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/cases/{case_resp.json()['id']}/status", params={"status": "resolved"}, headers=TENANT_HEADERS)
    assert resp.json()["status"] == "resolved"
    resolved_events = [p for e, p in published if e == "hrm.case_resolved"]
    assert len(resolved_events) == 1
    assert resolved_events[0]["employee_id"] == emp_id


async def test_non_resolved_status_change_does_not_trigger_note_update(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import case_management
    monkeypatch.setattr(case_management, "publish_event", spy)

    case_resp = await client.post("/api/v1/hrm/cases", json={"employee_id": str(uuid.uuid4()), "case_type": "policy_question", "subject": "x"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/hrm/cases/{case_resp.json()['id']}/status", params={"status": "in_progress"}, headers=TENANT_HEADERS)
    assert len(published) == 0


async def test_append_to_note_api_preserves_original_text(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    note_resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/notes", json={"body": "Original human note.", "author": "manager-jane"}, headers=TENANT_HEADERS)

    append_resp = await client.post(f"/api/v1/hrm/notes/{note_resp.json()['id']}/append", json={"additional_text": "Case resolved.", "author": "talent-agent"}, headers=TENANT_HEADERS)
    assert append_resp.status_code == 200
    assert "Original human note." in append_resp.json()["body"]
    assert "Case resolved." in append_resp.json()["body"]


async def test_append_to_unknown_note_404(client):
    resp = await client.post(f"/api/v1/hrm/notes/{uuid.uuid4()}/append", json={"additional_text": "x"}, headers=TENANT_HEADERS)
    assert resp.status_code == 404
