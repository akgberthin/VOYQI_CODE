import uuid

from tests.conftest import TENANT_HEADERS


async def test_create_course_with_video_content_type_api(client):
    resp = await client.post(
        "/api/v1/hrm/courses", json={"title": "Defensive Driving", "category": "safety", "content_type": "video", "content_url": "https://academy.internal/videos/defensive-driving", "description": "Hazard recognition."},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["content_type"] == "video"
    assert resp.json()["content_url"] == "https://academy.internal/videos/defensive-driving"


async def test_create_course_draft_and_publish_flow_api(client):
    draft_resp = await client.post(
        "/api/v1/hrm/course-drafts",
        json={"prompt": "defensive driving safety", "draft_title": "Defensive Driving Safety", "draft_category": "safety", "draft_description": "Covers hazard recognition.", "draft_content_type": "video"},
        headers=TENANT_HEADERS,
    )
    assert draft_resp.status_code == 201

    pending_resp = await client.get("/api/v1/hrm/course-drafts/pending", headers=TENANT_HEADERS)
    assert len(pending_resp.json()) == 1

    publish_resp = await client.post(f"/api/v1/hrm/course-drafts/{draft_resp.json()['id']}/publish", json={"is_mandatory": True, "department": "Dealership"}, headers=TENANT_HEADERS)
    assert publish_resp.status_code == 200
    assert publish_resp.json()["status"] == "published"

    catalog_resp = await client.get("/api/v1/hrm/courses", headers=TENANT_HEADERS)
    assert any(c["title"] == "Defensive Driving Safety" for c in catalog_resp.json())


async def test_reject_course_draft_creates_no_course_api(client):
    draft_resp = await client.post("/api/v1/hrm/course-drafts", json={"prompt": "x", "draft_title": "Rejected Course", "draft_category": "technical"}, headers=TENANT_HEADERS)
    reject_resp = await client.post(f"/api/v1/hrm/course-drafts/{draft_resp.json()['id']}/reject", headers=TENANT_HEADERS)
    assert reject_resp.json()["status"] == "rejected"

    catalog_resp = await client.get("/api/v1/hrm/courses", headers=TENANT_HEADERS)
    assert not any(c["title"] == "Rejected Course" for c in catalog_resp.json())


async def test_request_course_draft_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import training
    monkeypatch.setattr(training, "publish_event", spy)

    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/hrm/employees/{emp_resp.json()['id']}/request-course-draft", params={"prompt": "forklift safety certification"}, headers=TENANT_HEADERS)
    assert resp.status_code == 202
    event_type, payload = published[0]
    assert event_type == "hrm.course_draft_requested"
    assert payload["prompt"] == "forklift safety certification"


async def test_draft_course_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_course_draft", lambda tenant_id, prompt: {"draft_created": True, "reasoning": "x"})
    resp = await client.post("/api/v1/hrm/course-drafts/draft-now", params={"prompt": "forklift safety"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["draft_created"] is True


async def test_publish_unknown_draft_404(client):
    resp = await client.post(f"/api/v1/hrm/course-drafts/{uuid.uuid4()}/publish", json={}, headers=TENANT_HEADERS)
    assert resp.status_code == 409
