import uuid

from tests.conftest import TENANT_HEADERS


async def test_create_case_sets_status_by_type(client):
    resp = await client.post("/api/v1/hrm/cases", json={"employee_id": str(uuid.uuid4()), "case_type": "payroll_inquiry", "subject": "Missing overtime pay"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["status"] == "open"


async def test_create_complaint_case_auto_escalates(client):
    resp = await client.post("/api/v1/hrm/cases", json={"employee_id": str(uuid.uuid4()), "case_type": "complaint", "subject": "Workplace concern"}, headers=TENANT_HEADERS)
    assert resp.json()["status"] == "escalated"


async def test_inbound_policy_question_publishes_ai_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import case_management
    monkeypatch.setattr(case_management, "publish_event", spy)

    case_resp = await client.post("/api/v1/hrm/cases", json={"employee_id": str(uuid.uuid4()), "case_type": "policy_question", "subject": "PTO carryover"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/cases/{case_resp.json()['id']}/messages", json={"direction": "inbound", "body": "Can I carry over PTO?", "sender": "employee"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    event_type, payload = published[0]
    assert event_type == "hrm.policy_question_asked"
    assert payload["question"] == "Can I carry over PTO?"


async def test_inbound_complaint_message_never_publishes_ai_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import case_management
    monkeypatch.setattr(case_management, "publish_event", spy)

    case_resp = await client.post("/api/v1/hrm/cases", json={"employee_id": str(uuid.uuid4()), "case_type": "complaint", "subject": "Workplace concern"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/cases/{case_resp.json()['id']}/messages", json={"direction": "inbound", "body": "Details of the concern", "sender": "employee"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert len(published) == 0  # complaints never trigger an AI draft path


async def test_outbound_message_never_publishes_ai_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import case_management
    monkeypatch.setattr(case_management, "publish_event", spy)

    case_resp = await client.post("/api/v1/hrm/cases", json={"employee_id": str(uuid.uuid4()), "case_type": "policy_question", "subject": "PTO carryover"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/hrm/cases/{case_resp.json()['id']}/messages", json={"direction": "outbound", "body": "Answer", "sender": "hr"}, headers=TENANT_HEADERS)
    assert len(published) == 0


async def test_answer_policy_question_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_policy_answer", lambda tenant_id, case_id, question: {"draft_created": True, "reasoning": "x"})
    case_resp = await client.post("/api/v1/hrm/cases", json={"employee_id": str(uuid.uuid4()), "case_type": "policy_question", "subject": "PTO carryover"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/cases/{case_resp.json()['id']}/answer-policy-question-now", params={"question": "Can I carry over PTO?"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["draft_created"] is True


async def test_policy_document_catalog_api(client):
    resp = await client.post("/api/v1/hrm/policy-documents", json={"title": "PTO Policy", "body": "Up to 40 hours carryover annually.", "category": "time_off"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201

    list_resp = await client.get("/api/v1/hrm/policy-documents", params={"category": "time_off"}, headers=TENANT_HEADERS)
    assert len(list_resp.json()) == 1


async def test_case_draft_reply_approve_flow_api(client):
    case_resp = await client.post("/api/v1/hrm/cases", json={"employee_id": str(uuid.uuid4()), "case_type": "policy_question", "subject": "PTO carryover"}, headers=TENANT_HEADERS)

    draft_resp = await client.post(
        f"/api/v1/hrm/cases/{case_resp.json()['id']}/draft-replies", json={"draft_body": "You may carry over up to 40 hours.", "reasoning": "grounded in PTO Policy"}, headers=TENANT_HEADERS
    )
    assert draft_resp.status_code == 201

    pending_resp = await client.get(f"/api/v1/hrm/cases/{case_resp.json()['id']}/draft-replies/pending", headers=TENANT_HEADERS)
    assert len(pending_resp.json()) == 1

    approve_resp = await client.post(f"/api/v1/hrm/draft-replies/{draft_resp.json()['id']}/approve-and-send", json={}, headers=TENANT_HEADERS)
    assert approve_resp.status_code == 200

    messages_resp = await client.get(f"/api/v1/hrm/cases/{case_resp.json()['id']}/messages", headers=TENANT_HEADERS)
    outbound = [m for m in messages_resp.json() if m["direction"] == "outbound"]
    assert len(outbound) == 1


async def test_reject_case_draft_api(client):
    case_resp = await client.post("/api/v1/hrm/cases", json={"employee_id": str(uuid.uuid4()), "case_type": "policy_question", "subject": "Question"}, headers=TENANT_HEADERS)
    draft_resp = await client.post(f"/api/v1/hrm/cases/{case_resp.json()['id']}/draft-replies", json={"draft_body": "x"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/draft-replies/{draft_resp.json()['id']}/reject", headers=TENANT_HEADERS)
    assert resp.json()["status"] == "rejected"


async def test_list_cases_by_status_api(client):
    await client.post("/api/v1/hrm/cases", json={"employee_id": str(uuid.uuid4()), "case_type": "complaint", "subject": "x"}, headers=TENANT_HEADERS)
    resp = await client.get("/api/v1/hrm/cases", params={"status": "escalated"}, headers=TENANT_HEADERS)
    assert len(resp.json()) >= 1
    assert all(c["status"] == "escalated" for c in resp.json())
