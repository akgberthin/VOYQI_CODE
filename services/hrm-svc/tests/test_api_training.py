import uuid

from tests.conftest import TENANT_HEADERS


async def test_request_course_recommendation_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import training
    monkeypatch.setattr(training, "publish_event", spy)

    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe", "department": "Sales"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/hrm/courses", json={"title": "Anti-Harassment Training", "category": "compliance", "is_mandatory": True}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/request-course-recommendation", headers=TENANT_HEADERS)
    assert resp.status_code == 202
    event_type, payload = published[0]
    assert event_type == "hrm.course_recommendation_requested"
    assert "Anti-Harassment Training" in payload["incomplete_mandatory_course_titles"]


async def test_request_course_recommendation_unknown_employee_404(client):
    resp = await client.post(f"/api/v1/hrm/employees/{uuid.uuid4()}/request-course-recommendation", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_recommend_course_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(
        ai_orchestration_tools, "request_course_recommendation",
        lambda tenant_id, employee_id, ids, titles: {"recommendation_created": True, "reasoning": "x"},
    )
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/recommend-course-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["recommendation_created"] is True


async def test_full_course_and_completion_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    course_resp = await client.post("/api/v1/hrm/courses", json={"title": "Safety 101", "category": "safety", "duration_hours": 2.0}, headers=TENANT_HEADERS)

    completion_resp = await client.post(
        f"/api/v1/hrm/employees/{emp_resp.json()['id']}/course-completions", params={"course_id": course_resp.json()["id"]}, headers=TENANT_HEADERS
    )
    assert completion_resp.json()["status"] == "in_progress"

    complete_resp = await client.post(f"/api/v1/hrm/course-completions/{completion_resp.json()['id']}/complete", headers=TENANT_HEADERS)
    assert complete_resp.json()["status"] == "completed"

    list_resp = await client.get(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/course-completions", headers=TENANT_HEADERS)
    assert len(list_resp.json()) == 1


async def test_covenant_acknowledgment_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)

    ack_resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/covenant-acknowledgments", json={"covenant_version": "v2026.1"}, headers=TENANT_HEADERS)
    assert ack_resp.status_code == 201

    list_resp = await client.get(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/covenant-acknowledgments", headers=TENANT_HEADERS)
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["covenant_version"] == "v2026.1"


async def test_course_recommendation_accept_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    course_resp = await client.post("/api/v1/hrm/courses", json={"title": "Data Security Basics", "category": "compliance", "is_mandatory": True}, headers=TENANT_HEADERS)

    rec_resp = await client.post(
        f"/api/v1/hrm/employees/{emp_resp.json()['id']}/course-recommendations",
        json={"recommended_course_id": course_resp.json()["id"], "reasoning": "mandatory compliance course not yet completed"},
        headers=TENANT_HEADERS,
    )
    assert rec_resp.status_code == 201

    pending_resp = await client.get(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/course-recommendations/pending", headers=TENANT_HEADERS)
    assert len(pending_resp.json()) == 1

    accept_resp = await client.post(f"/api/v1/hrm/course-recommendations/{rec_resp.json()['id']}/accept", headers=TENANT_HEADERS)
    assert accept_resp.status_code == 200
    assert accept_resp.json()["completion_id"] is not None


async def test_course_recommendation_reject_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    rec_resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/course-recommendations", json={"reasoning": "no gaps found"}, headers=TENANT_HEADERS)

    reject_resp = await client.post(f"/api/v1/hrm/course-recommendations/{rec_resp.json()['id']}/reject", headers=TENANT_HEADERS)
    assert reject_resp.json()["status"] == "rejected"
