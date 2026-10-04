import uuid

from tests.conftest import TENANT_HEADERS


async def test_close_review_cycle_publishes_nine_box_events(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import performance
    monkeypatch.setattr(performance, "publish_event", spy)

    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    cycle_resp = await client.post("/api/v1/hrm/review-cycles", json={"name": "2026 H1", "start_date": "2026-01-01T00:00:00Z", "end_date": "2026-06-30T00:00:00Z"}, headers=TENANT_HEADERS)
    await client.post(
        "/api/v1/hrm/reviews", json={"employee_id": emp_resp.json()["id"], "review_cycle_id": cycle_resp.json()["id"], "rating": 4.2, "manager_notes": "strong quarter"}, headers=TENANT_HEADERS
    )

    resp = await client.post(f"/api/v1/hrm/review-cycles/{cycle_resp.json()['id']}/close", headers=TENANT_HEADERS)
    assert resp.json()["status"] == "closed"
    nine_box_events = [p for e, p in published if e == "hrm.nine_box_evaluation_requested"]
    assert len(nine_box_events) == 1
    assert nine_box_events[0]["rating"] == 4.2


async def test_request_feedback_draft_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import performance
    monkeypatch.setattr(performance, "publish_event", spy)

    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    cycle_resp = await client.post("/api/v1/hrm/review-cycles", json={"name": "2026 H1", "start_date": "2026-01-01T00:00:00Z", "end_date": "2026-06-30T00:00:00Z"}, headers=TENANT_HEADERS)
    review_resp = await client.post(
        "/api/v1/hrm/reviews", json={"employee_id": emp_resp.json()["id"], "review_cycle_id": cycle_resp.json()["id"], "manager_notes": "strong quarter, exceeded CSI"}, headers=TENANT_HEADERS
    )

    resp = await client.post(f"/api/v1/hrm/reviews/{review_resp.json()['id']}/request-feedback-draft", headers=TENANT_HEADERS)
    assert resp.status_code == 202
    event_type, payload = published[0]
    assert event_type == "hrm.feedback_draft_requested"
    assert payload["manager_notes"] == "strong quarter, exceeded CSI"


async def test_full_goal_and_review_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    employee_id = emp_resp.json()["id"]

    goal_resp = await client.post(f"/api/v1/hrm/employees/{employee_id}/goals", json={"title": "Increase CSI score"}, headers=TENANT_HEADERS)
    assert goal_resp.status_code == 201

    await client.post(f"/api/v1/hrm/goals/{goal_resp.json()['id']}/status", params={"status": "completed"}, headers=TENANT_HEADERS)

    rate_resp = await client.get(f"/api/v1/hrm/employees/{employee_id}/goal-completion-rate", headers=TENANT_HEADERS)
    assert rate_resp.json()["completion_rate"] == 1.0


async def test_feedback_draft_approve_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    cycle_resp = await client.post("/api/v1/hrm/review-cycles", json={"name": "2026 H1", "start_date": "2026-01-01T00:00:00Z", "end_date": "2026-06-30T00:00:00Z"}, headers=TENANT_HEADERS)
    review_resp = await client.post("/api/v1/hrm/reviews", json={"employee_id": emp_resp.json()["id"], "review_cycle_id": cycle_resp.json()["id"]}, headers=TENANT_HEADERS)

    draft_resp = await client.post(
        f"/api/v1/hrm/reviews/{review_resp.json()['id']}/feedback-drafts", json={"draft_body": "Jane had an excellent quarter.", "reasoning": "polished from manager notes"}, headers=TENANT_HEADERS
    )
    assert draft_resp.status_code == 201

    pending_resp = await client.get(f"/api/v1/hrm/reviews/{review_resp.json()['id']}/feedback-drafts/pending", headers=TENANT_HEADERS)
    assert len(pending_resp.json()) == 1

    approve_resp = await client.post(f"/api/v1/hrm/feedback-drafts/{draft_resp.json()['id']}/approve", json={}, headers=TENANT_HEADERS)
    assert approve_resp.status_code == 200
    assert approve_resp.json()["finalized_feedback"] == "Jane had an excellent quarter."


async def test_nine_box_confirm_flow_api(client):
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    cycle_resp = await client.post("/api/v1/hrm/review-cycles", json={"name": "2026 H1", "start_date": "2026-01-01T00:00:00Z", "end_date": "2026-06-30T00:00:00Z"}, headers=TENANT_HEADERS)

    placement_resp = await client.post(
        "/api/v1/hrm/nine-box-placements",
        json={"employee_id": emp_resp.json()["id"], "review_cycle_id": cycle_resp.json()["id"], "performance_axis": "high", "potential_axis": "high", "reasoning": "top performer"},
        headers=TENANT_HEADERS,
    )
    assert placement_resp.status_code == 201

    grid_resp = await client.get(f"/api/v1/hrm/review-cycles/{cycle_resp.json()['id']}/nine-box-grid", headers=TENANT_HEADERS)
    assert len(grid_resp.json()) == 1

    confirm_resp = await client.post(f"/api/v1/hrm/nine-box-placements/{placement_resp.json()['id']}/confirm", headers=TENANT_HEADERS)
    assert confirm_resp.json()["status"] == "confirmed"


async def test_request_feedback_draft_unknown_review_404(client):
    resp = await client.post(f"/api/v1/hrm/reviews/{uuid.uuid4()}/request-feedback-draft", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_draft_feedback_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(
        ai_orchestration_tools, "request_feedback_draft",
        lambda tenant_id, review_id, manager_notes, rating: {"draft_created": True, "reasoning": "x"},
    )
    emp_resp = await client.post("/api/v1/hrm/employees", json={"full_name": "Jane Doe"}, headers=TENANT_HEADERS)
    cycle_resp = await client.post("/api/v1/hrm/review-cycles", json={"name": "2026 H1", "start_date": "2026-01-01T00:00:00Z", "end_date": "2026-06-30T00:00:00Z"}, headers=TENANT_HEADERS)
    review_resp = await client.post("/api/v1/hrm/reviews", json={"employee_id": emp_resp.json()["id"], "review_cycle_id": cycle_resp.json()["id"]}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/hrm/reviews/{review_resp.json()['id']}/draft-feedback-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["draft_created"] is True


async def test_draft_feedback_now_unknown_review_404(client):
    resp = await client.post(f"/api/v1/hrm/reviews/{uuid.uuid4()}/draft-feedback-now", headers=TENANT_HEADERS)
    assert resp.status_code == 404
