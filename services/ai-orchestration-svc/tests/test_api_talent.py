from src.services import hrm_tools
from tests.conftest import TENANT_HEADERS


async def test_evaluate_checklist_population_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "add_checklist_task", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-checklist-population",
        json={"checklist_id": "checklist-1", "kind": "onboarding", "job_title": "Service Advisor", "department": "dealership"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert "Provision DMS system access" in resp.json()["tasks_added"]


async def test_evaluate_benefits_recommendation_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        hrm_tools, "get_benefits_plans",
        lambda tenant_id: {"plans": [{"id": "plan-1", "name": "VOYQI PPO Gold", "plan_type": "medical", "monthly_premium_usd": 450.0, "employer_contribution_pct": 0.7}]},
    )
    monkeypatch.setattr(hrm_tools, "create_benefits_recommendation", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-benefits-recommendation",
        json={"employee_id": "emp-1", "job_title": "Service Advisor", "department": "dealership"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["recommended_plan_id"] == "plan-1"


async def test_evaluate_screening_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "apply_screening_score", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-screening",
        json={"application_id": "app-1", "resume_summary": "automotive diagnostics", "required_skills": "automotive"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["score"] == 1.0


async def test_evaluate_nurture_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_nurture_draft", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-nurture",
        json={"candidate_id": "cand-1", "candidate_name": "Alex Chen", "reason": "role filled"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["draft_created"] is True


async def test_evaluate_feedback_draft_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_feedback_draft", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-feedback-draft",
        json={"review_id": "review-1", "manager_notes": "strong quarter", "rating": 4.2},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["draft_created"] is True


async def test_evaluate_nine_box_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_nine_box_placement", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-nine-box",
        json={"employee_id": "emp-1", "review_cycle_id": "cycle-1", "rating": 4.5, "goal_completion_rate": 0.9},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["performance_axis"] == "high"


async def test_evaluate_attrition_risk_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "record_attrition_risk", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-attrition-risk",
        json={"employee_id": "emp-1", "comp_band_position": 0.15, "months_since_last_comp_change": 6},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["risk_score"] == 0.35


async def test_evaluate_course_recommendation_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_course_recommendation", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-course-recommendation",
        json={"employee_id": "emp-1", "incomplete_mandatory_course_ids": ["course-compliance"], "incomplete_mandatory_course_titles": ["Anti-Harassment Compliance Training"]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["recommended_course_id"] == "course-compliance"


async def test_evaluate_policy_question_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_policy_documents", lambda tenant_id: [{"id": "policy-1", "title": "PTO Policy", "body": "Up to 40 hours carryover annually."}])
    monkeypatch.setattr(hrm_tools, "create_case_draft_reply", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-policy-question",
        json={"case_id": "case-1", "question": "Can I carry over unused PTO?"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["draft_created"] is True


async def test_evaluate_note_update_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "get_most_recent_note", lambda tenant_id, employee_id: None)
    monkeypatch.setattr(hrm_tools, "add_employee_note", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-note-update",
        json={"employee_id": "emp-1", "context": "Completed onboarding"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["note_written"] is True
    assert resp.json()["action_taken"] == "note_added"


async def test_evaluate_email_draft_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_email_draft", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-email-draft",
        json={"recipient_scope": "all_staff", "prompt": "remind everyone open enrollment ends Friday"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["email_created"] is True


async def test_evaluate_course_draft_endpoint(client, monkeypatch):
    monkeypatch.setattr(hrm_tools, "create_course_draft", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/hrm/evaluate-course-draft",
        json={"prompt": "forklift safety certification"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["draft_created"] is True
