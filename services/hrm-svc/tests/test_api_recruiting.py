import uuid

from tests.conftest import TENANT_HEADERS


async def test_application_submission_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import recruiting
    monkeypatch.setattr(recruiting, "publish_event", spy)

    candidate_resp = await client.post("/api/v1/hrm/candidates", json={"full_name": "Alex Chen", "resume_summary": "automotive diagnostics, customer service"}, headers=TENANT_HEADERS)
    req_resp = await client.post("/api/v1/hrm/requisitions", json={"title": "Service Advisor", "required_skills": "automotive, customer service"}, headers=TENANT_HEADERS)

    resp = await client.post("/api/v1/hrm/applications", json={"candidate_id": candidate_resp.json()["id"], "requisition_id": req_resp.json()["id"]}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    event_type, payload = published[0]
    assert event_type == "hrm.application_submitted"
    assert payload["required_skills"] == "automotive, customer service"


async def test_application_rejection_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import recruiting
    monkeypatch.setattr(recruiting, "publish_event", spy)

    candidate_resp = await client.post("/api/v1/hrm/candidates", json={"full_name": "Alex Chen"}, headers=TENANT_HEADERS)
    req_resp = await client.post("/api/v1/hrm/requisitions", json={"title": "Service Advisor"}, headers=TENANT_HEADERS)
    app_resp = await client.post("/api/v1/hrm/applications", json={"candidate_id": candidate_resp.json()["id"], "requisition_id": req_resp.json()["id"]}, headers=TENANT_HEADERS)
    published.clear()

    resp = await client.post(f"/api/v1/hrm/applications/{app_resp.json()['id']}/reject", json={"reason": "insufficient experience"}, headers=TENANT_HEADERS)
    assert resp.json()["stage"] == "rejected"
    rejected_events = [p for e, p in published if e == "hrm.application_rejected"]
    assert len(rejected_events) == 1


async def test_full_pipeline_flow_api(client):
    candidate_resp = await client.post("/api/v1/hrm/candidates", json={"full_name": "Alex Chen"}, headers=TENANT_HEADERS)
    req_resp = await client.post("/api/v1/hrm/requisitions", json={"title": "Service Advisor"}, headers=TENANT_HEADERS)
    app_resp = await client.post("/api/v1/hrm/applications", json={"candidate_id": candidate_resp.json()["id"], "requisition_id": req_resp.json()["id"]}, headers=TENANT_HEADERS)
    application_id = app_resp.json()["id"]

    score_resp = await client.post(f"/api/v1/hrm/applications/{application_id}/apply-screening-score", params={"score": 0.82, "reasoning": "strong match"}, headers=TENANT_HEADERS)
    assert score_resp.json()["stage"] == "screening"

    advance_resp = await client.post(f"/api/v1/hrm/applications/{application_id}/advance-stage", json={"stage": "interview"}, headers=TENANT_HEADERS)
    assert advance_resp.json()["stage"] == "interview"

    hired_resp = await client.post(f"/api/v1/hrm/applications/{application_id}/advance-stage", json={"stage": "hired"}, headers=TENANT_HEADERS)
    assert hired_resp.json()["stage"] == "hired"


async def test_nurture_draft_flow_api(client):
    candidate_resp = await client.post("/api/v1/hrm/candidates", json={"full_name": "Alex Chen"}, headers=TENANT_HEADERS)
    candidate_id = candidate_resp.json()["id"]

    draft_resp = await client.post(
        f"/api/v1/hrm/candidates/{candidate_id}/nurture-drafts",
        json={"draft_body": "Thanks for applying! We'd love to keep you in mind for future roles.", "reasoning": "strong candidate, role filled by someone else"},
        headers=TENANT_HEADERS,
    )
    assert draft_resp.status_code == 201

    pending_resp = await client.get(f"/api/v1/hrm/candidates/{candidate_id}/nurture-drafts/pending", headers=TENANT_HEADERS)
    assert len(pending_resp.json()) == 1

    approve_resp = await client.post(f"/api/v1/hrm/nurture-drafts/{draft_resp.json()['id']}/approve-and-send", json={}, headers=TENANT_HEADERS)
    assert approve_resp.status_code == 200

    timeline_resp = await client.get(f"/api/v1/hrm/candidates/{candidate_id}/timeline", headers=TENANT_HEADERS)
    nurture_events = [e for e in timeline_resp.json() if e["interaction_type"] == "nurture_message"]
    assert len(nurture_events) == 1


async def test_reject_nurture_draft_api(client):
    candidate_resp = await client.post("/api/v1/hrm/candidates", json={"full_name": "Alex Chen"}, headers=TENANT_HEADERS)
    draft_resp = await client.post(f"/api/v1/hrm/candidates/{candidate_resp.json()['id']}/nurture-drafts", json={"draft_body": "x"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/nurture-drafts/{draft_resp.json()['id']}/reject", headers=TENANT_HEADERS)
    assert resp.json()["status"] == "rejected"


async def test_sourcing_channel_analytics_api(client):
    await client.post("/api/v1/hrm/candidates", json={"full_name": "A", "source_channel": "referral"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/hrm/candidates", json={"full_name": "B", "source_channel": "job_board"}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/hrm/analytics/sourcing-channels", headers=TENANT_HEADERS)
    assert len(resp.json()) == 2


async def test_close_requisition_api(client):
    req_resp = await client.post("/api/v1/hrm/requisitions", json={"title": "Service Advisor"}, headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/hrm/requisitions/{req_resp.json()['id']}/close", params={"status": "filled"}, headers=TENANT_HEADERS)
    assert resp.json()["status"] == "filled"


async def test_submit_application_unknown_candidate_404(client):
    req_resp = await client.post("/api/v1/hrm/requisitions", json={"title": "Service Advisor"}, headers=TENANT_HEADERS)
    resp = await client.post("/api/v1/hrm/applications", json={"candidate_id": str(uuid.uuid4()), "requisition_id": req_resp.json()["id"]}, headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_screen_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(
        ai_orchestration_tools, "request_screening",
        lambda tenant_id, application_id, resume_summary, required_skills: {"score": 0.8, "reasoning": "x"},
    )
    candidate_resp = await client.post("/api/v1/hrm/candidates", json={"full_name": "Alex Chen"}, headers=TENANT_HEADERS)
    req_resp = await client.post("/api/v1/hrm/requisitions", json={"title": "Service Advisor"}, headers=TENANT_HEADERS)
    app_resp = await client.post("/api/v1/hrm/applications", json={"candidate_id": candidate_resp.json()["id"], "requisition_id": req_resp.json()["id"]}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/applications/{app_resp.json()['id']}/screen-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["score"] == 0.8


async def test_screen_now_unknown_application_404(client):
    resp = await client.post(f"/api/v1/hrm/applications/{uuid.uuid4()}/screen-now", headers=TENANT_HEADERS)
    assert resp.status_code == 404
