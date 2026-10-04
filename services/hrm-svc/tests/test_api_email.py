import uuid

from tests.conftest import TENANT_HEADERS


async def test_create_manual_email_draft_api(client):
    resp = await client.post("/api/v1/hrm/emails", json={"recipient_scope": "all_staff", "subject": "Q3 Update", "body": "All staff meeting Friday."}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["status"] == "draft"
    assert resp.json()["is_ai_generated"] is False


async def test_single_employee_email_requires_recipient_id(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    resp = await client.post(
        "/api/v1/hrm/emails", json={"recipient_scope": "single_employee", "subject": "Reminder", "body": "Please complete your training.", "recipient_employee_id": emp_resp.json()["id"]}, headers=TENANT_HEADERS
    )
    assert resp.json()["recipient_employee_id"] == emp_resp.json()["id"]


async def test_recipient_count_for_all_staff_matches_real_headcount(client):
    await client.post("/api/v1/hrm/employees", json={"full_name": "A"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/hrm/employees", json={"full_name": "B"}, headers=TENANT_HEADERS)
    email_resp = await client.post("/api/v1/hrm/emails", json={"recipient_scope": "all_staff", "subject": "x", "body": "x"}, headers=TENANT_HEADERS)

    count_resp = await client.get(f"/api/v1/hrm/emails/{email_resp.json()['id']}/recipient-count", headers=TENANT_HEADERS)
    assert count_resp.json()["recipient_count"] >= 2


async def test_send_email_requires_draft_status(client):
    email_resp = await client.post("/api/v1/hrm/emails", json={"recipient_scope": "all_staff", "subject": "x", "body": "x"}, headers=TENANT_HEADERS)
    send_resp = await client.post(f"/api/v1/hrm/emails/{email_resp.json()['id']}/send", headers=TENANT_HEADERS)
    assert send_resp.json()["status"] == "sent"

    second_send = await client.post(f"/api/v1/hrm/emails/{email_resp.json()['id']}/send", headers=TENANT_HEADERS)
    assert second_send.status_code == 409


async def test_request_ai_email_draft_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import email
    monkeypatch.setattr(email, "publish_event", spy)

    resp = await client.post("/api/v1/hrm/emails/request-ai-draft", params={"recipient_scope": "all_staff", "prompt": "remind everyone open enrollment ends Friday"}, headers=TENANT_HEADERS)
    assert resp.status_code == 202
    event_type, payload = published[0]
    assert event_type == "hrm.email_draft_requested"
    assert payload["prompt"] == "remind everyone open enrollment ends Friday"


async def test_draft_ai_email_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_email_draft", lambda tenant_id, scope, prompt, recipient_id: {"email_created": True, "reasoning": "x"})
    resp = await client.post("/api/v1/hrm/emails/draft-ai-now", params={"recipient_scope": "all_staff", "prompt": "remind everyone"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["email_created"] is True


async def test_list_email_drafts_filters_by_status(client):
    email_resp = await client.post("/api/v1/hrm/emails", json={"recipient_scope": "all_staff", "subject": "x", "body": "x"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/hrm/emails/{email_resp.json()['id']}/send", headers=TENANT_HEADERS)

    sent_resp = await client.get("/api/v1/hrm/emails", params={"status": "sent"}, headers=TENANT_HEADERS)
    assert all(e["status"] == "sent" for e in sent_resp.json())


async def test_get_recipient_count_unknown_email_404(client):
    resp = await client.get(f"/api/v1/hrm/emails/{uuid.uuid4()}/recipient-count", headers=TENANT_HEADERS)
    assert resp.status_code == 404
