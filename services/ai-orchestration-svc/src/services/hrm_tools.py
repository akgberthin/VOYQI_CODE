"""
HRM tool adapters — used by the Talent Agent.

add_checklist_task is the only way a task gets added to an onboarding/
offboarding checklist from an agent's recommendation — a suggestion the
checklist's own hard gate (mark_checklist_complete) still enforces
regardless of who added the task or how confident the recommendation
was.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": settings.ai_initiated_header_value}


def get_benefits_plans(tenant_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.get("/api/v1/hrm/benefits-plans", headers=_headers(tenant_id))
            resp.raise_for_status()
            return {"plans": resp.json()}
    except httpx.HTTPError:
        return {"plans": []}


def get_employee_benefits_enrollments(tenant_id: str, employee_id: str) -> list[dict[str, Any]]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.get(f"/api/v1/hrm/employees/{employee_id}/benefits-enrollments", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return []


def accept_benefits_recommendation(tenant_id: str, recommendation_id: str) -> dict[str, Any]:
    """The autonomous-execution counterpart to create_benefits_recommendation —
    called only when the financial-risk check in talent_agent.py finds no
    cost increase versus the employee's current active enrollment."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(f"/api/v1/hrm/benefits-recommendations/{recommendation_id}/accept", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def create_benefits_recommendation(tenant_id: str, employee_id: str, recommended_plan_id: str, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/hrm/employees/{employee_id}/benefits-recommendations",
                json={"recommended_plan_id": recommended_plan_id, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_benefits_recommendation(recommended_plan_id: str | None, reasoning: str) -> dict[str, Any]:
    return {"recommended_plan_id": recommended_plan_id, "reasoning": reasoning}


BENEFITS_RECOMMENDATION_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_benefits_plans",
        "description": "Fetch every benefits plan in the catalog (type, tier, premium, employer contribution).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_benefits_recommendation",
        "description": (
            "Report the recommended plan id (or null if none fits well), and your reasoning. "
            "Always call this to conclude."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "recommended_plan_id": {"type": ["string", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["recommended_plan_id", "reasoning"],
        },
    },
]


def build_benefits_recommendation_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_benefits_plans": lambda inp: get_benefits_plans(tenant_id),
        "submit_benefits_recommendation": lambda inp: submit_benefits_recommendation(inp.get("recommended_plan_id"), inp["reasoning"]),
    }


def apply_screening_score(tenant_id: str, application_id: str, score: float, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/hrm/applications/{application_id}/apply-screening-score",
                params={"score": score, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_screening_score(score: float, reasoning: str) -> dict[str, Any]:
    return {"score": score, "reasoning": reasoning}


SCREENING_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_screening_score",
        "description": "Report a resume-fit score 0.0-1.0 and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["score", "reasoning"],
        },
    },
]


def build_screening_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_screening_score": lambda inp: submit_screening_score(inp["score"], inp["reasoning"]),
    }


def create_nurture_draft(tenant_id: str, candidate_id: str, draft_body: str, reasoning: str = "", application_id: str = "") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/hrm/candidates/{candidate_id}/nurture-drafts",
                json={"draft_body": draft_body, "reasoning": reasoning, "application_id": application_id or None},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def send_nurture_draft(tenant_id: str, draft_id: str) -> dict[str, Any]:
    """Full-automation path — the agent auto-approves and sends its own
    nurture draft immediately after creating it. Financial-risk carve-out
    does not apply here (no cost commitment); this is safe to automate."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(f"/api/v1/hrm/nurture-drafts/{draft_id}/approve-and-send", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_nurture_draft(draft_body: str, reasoning: str) -> dict[str, Any]:
    return {"draft_body": draft_body, "reasoning": reasoning}


NURTURE_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_nurture_draft",
        "description": "Report the drafted candidate outreach message and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_body": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["draft_body", "reasoning"],
        },
    },
]


def build_nurture_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_nurture_draft": lambda inp: submit_nurture_draft(inp["draft_body"], inp["reasoning"]),
    }


def create_feedback_draft(tenant_id: str, review_id: str, draft_body: str, reasoning: str = "") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/hrm/reviews/{review_id}/feedback-drafts",
                json={"draft_body": draft_body, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def finalize_feedback_draft(tenant_id: str, draft_id: str) -> dict[str, Any]:
    """Full-automation path — auto-finalizes the agent's own drafted
    feedback text unchanged. No financial or security risk: this
    polishes wording, never the underlying rating."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(f"/api/v1/hrm/feedback-drafts/{draft_id}/approve", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_feedback_draft(draft_body: str, reasoning: str) -> dict[str, Any]:
    return {"draft_body": draft_body, "reasoning": reasoning}


FEEDBACK_DRAFT_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_feedback_draft",
        "description": "Report the polished narrative feedback drafted from the manager's raw notes, and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_body": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["draft_body", "reasoning"],
        },
    },
]


def build_feedback_draft_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_feedback_draft": lambda inp: submit_feedback_draft(inp["draft_body"], inp["reasoning"]),
    }


def create_nine_box_placement(tenant_id: str, employee_id: str, review_cycle_id: str, performance_axis: str, potential_axis: str, reasoning: str = "") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/hrm/nine-box-placements",
                json={"employee_id": employee_id, "review_cycle_id": review_cycle_id, "performance_axis": performance_axis, "potential_axis": potential_axis, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_nine_box_placement(performance_axis: str, potential_axis: str, reasoning: str) -> dict[str, Any]:
    return {"performance_axis": performance_axis, "potential_axis": potential_axis, "reasoning": reasoning}


def confirm_nine_box_placement(tenant_id: str, placement_id: str) -> dict[str, Any]:
    """Full-automation path — auto-confirms the agent's own placement.
    A talent-calibration categorization, not itself a financial
    transaction, so no financial-risk exception applies."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(f"/api/v1/hrm/nine-box-placements/{placement_id}/confirm", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


NINE_BOX_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_nine_box_placement",
        "description": "Report the performance axis (low/medium/high), potential axis (low/medium/high), and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "performance_axis": {"type": "string", "enum": ["low", "medium", "high"]},
                "potential_axis": {"type": "string", "enum": ["low", "medium", "high"]},
                "reasoning": {"type": "string"},
            },
            "required": ["performance_axis", "potential_axis", "reasoning"],
        },
    },
]


def build_nine_box_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_nine_box_placement": lambda inp: submit_nine_box_placement(inp["performance_axis"], inp["potential_axis"], inp["reasoning"]),
    }


def record_attrition_risk(tenant_id: str, employee_id: str, risk_score: float, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/hrm/employees/{employee_id}/attrition-risk-scores",
                params={"risk_score": risk_score, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_attrition_risk(risk_score: float, reasoning: str) -> dict[str, Any]:
    return {"risk_score": risk_score, "reasoning": reasoning}


ATTRITION_RISK_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_attrition_risk",
        "description": "Report an attrition risk score 0.0-1.0 (higher = more likely to leave) and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "risk_score": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["risk_score", "reasoning"],
        },
    },
]


def build_attrition_risk_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_attrition_risk": lambda inp: submit_attrition_risk(inp["risk_score"], inp["reasoning"]),
    }


def create_course_recommendation(tenant_id: str, employee_id: str, recommended_course_id: str | None, reasoning: str = "") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/hrm/employees/{employee_id}/course-recommendations",
                json={"recommended_course_id": recommended_course_id, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_course_recommendation(recommended_course_id: str | None, reasoning: str) -> dict[str, Any]:
    return {"recommended_course_id": recommended_course_id, "reasoning": reasoning}


def accept_course_recommendation(tenant_id: str, recommendation_id: str) -> dict[str, Any]:
    """Full-automation path — auto-enrolls the employee in the agent's
    own recommendation immediately. VOYQI Academy courses carry no
    external cost field in this build, so no financial-risk exception
    applies to enrollment itself."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(f"/api/v1/hrm/course-recommendations/{recommendation_id}/accept", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


COURSE_RECOMMENDATION_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_course_recommendation",
        "description": "Report the recommended course ID (or null if none apply) and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recommended_course_id": {"type": ["string", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["recommended_course_id", "reasoning"],
        },
    },
]


def build_course_recommendation_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_course_recommendation": lambda inp: submit_course_recommendation(inp.get("recommended_course_id"), inp["reasoning"]),
    }


def get_policy_documents(tenant_id: str) -> list[dict[str, Any]]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.get("/api/v1/hrm/policy-documents", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return []


def create_case_draft_reply(tenant_id: str, case_id: str, draft_body: str, reasoning: str = "", cites_policy_id: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/hrm/cases/{case_id}/draft-replies",
                json={"draft_body": draft_body, "reasoning": reasoning, "cites_policy_id": cites_policy_id},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def send_case_reply(tenant_id: str, draft_id: str) -> dict[str, Any]:
    """Full-automation path — auto-sends the agent's own reply the
    moment it's grounded in a real policy document. No financial or
    security risk: policy_question is a category that already never
    reaches this point ungrounded (no draft is created at all when
    nothing grounds an answer)."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(f"/api/v1/hrm/draft-replies/{draft_id}/approve-and-send", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_policy_answer(draft_body: str, reasoning: str, cites_policy_id: str | None) -> dict[str, Any]:
    return {"draft_body": draft_body, "reasoning": reasoning, "cites_policy_id": cites_policy_id}


POLICY_ANSWER_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_policy_answer",
        "description": "Report the drafted answer (empty string if none of the policies apply), which policy ID it cites (null if none), and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_body": {"type": "string"},
                "cites_policy_id": {"type": ["string", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["draft_body", "cites_policy_id", "reasoning"],
        },
    },
]


def build_policy_answer_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_policy_answer": lambda inp: submit_policy_answer(inp["draft_body"], inp["reasoning"], inp.get("cites_policy_id")),
    }


def get_most_recent_note(tenant_id: str, employee_id: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.get(f"/api/v1/hrm/employees/{employee_id}/notes", headers=_headers(tenant_id))
            resp.raise_for_status()
            notes = resp.json()
            return notes[0] if notes else None
    except httpx.HTTPError:
        return None


def add_employee_note(tenant_id: str, employee_id: str, body: str, author: str = "talent-agent") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/hrm/employees/{employee_id}/notes",
                json={"body": body, "author": author},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def append_employee_note(tenant_id: str, note_id: str, additional_text: str, author: str = "talent-agent") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/hrm/notes/{note_id}/append",
                json={"additional_text": additional_text, "author": author},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_note_entry(entry_text: str, append_to_existing: bool, reasoning: str) -> dict[str, Any]:
    return {"entry_text": entry_text, "append_to_existing": append_to_existing, "reasoning": reasoning}


NOTE_ENTRY_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_note_entry",
        "description": "Report the note text to log, whether to append it to the most recent existing note (true) or start a new note (false), and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_text": {"type": "string"},
                "append_to_existing": {"type": "boolean"},
                "reasoning": {"type": "string"},
            },
            "required": ["entry_text", "append_to_existing", "reasoning"],
        },
    },
]


def build_note_entry_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_note_entry": lambda inp: submit_note_entry(inp["entry_text"], inp["append_to_existing"], inp["reasoning"]),
    }


def create_email_draft(tenant_id: str, recipient_scope: str, subject: str, body: str, recipient_employee_id: str | None = None, ai_prompt: str = "") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/hrm/emails",
                json={"recipient_scope": recipient_scope, "subject": subject, "body": body, "recipient_employee_id": recipient_employee_id},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_email_draft(subject: str, body: str, reasoning: str) -> dict[str, Any]:
    return {"subject": subject, "body": body, "reasoning": reasoning}


def send_email(tenant_id: str, email_id: str) -> dict[str, Any]:
    """Full-automation path — auto-sends the agent's own drafted
    email immediately. No financial or security risk in dispatching
    a formal communication itself."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(f"/api/v1/hrm/emails/{email_id}/send", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


EMAIL_DRAFT_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_email_draft",
        "description": "Report the drafted email subject and body, and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["subject", "body", "reasoning"],
        },
    },
]


def build_email_draft_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_email_draft": lambda inp: submit_email_draft(inp["subject"], inp["body"], inp["reasoning"]),
    }


def create_course_draft(tenant_id: str, prompt: str, title: str, category: str, description: str = "", content_type: str = "document", reasoning: str = "") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/hrm/course-drafts",
                json={"prompt": prompt, "draft_title": title, "draft_category": category, "draft_description": description, "draft_content_type": content_type, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_course_draft(title: str, category: str, description: str, content_type: str, reasoning: str) -> dict[str, Any]:
    return {"title": title, "category": category, "description": description, "content_type": content_type, "reasoning": reasoning}


def publish_course_draft(tenant_id: str, draft_id: str) -> dict[str, Any]:
    """Full-automation path — auto-publishes the agent's own course
    draft to the catalog immediately. Catalog curation carries no
    financial or security risk on its own; is_mandatory defaults to
    False so nothing becomes a required course without a person
    explicitly setting that later."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(f"/api/v1/hrm/course-drafts/{draft_id}/publish", json={}, headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


COURSE_DRAFT_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_course_draft",
        "description": "Report the drafted course title, category, description, content type, and your reasoning. Always call this to conclude.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "category": {"type": "string", "enum": ["compliance", "safety", "technical", "leadership"]},
                "description": {"type": "string"},
                "content_type": {"type": "string", "enum": ["video", "document", "quiz", "in_person", "other"]},
                "reasoning": {"type": "string"},
            },
            "required": ["title", "category", "description", "content_type", "reasoning"],
        },
    },
]


def build_course_draft_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_course_draft": lambda inp: submit_course_draft(inp["title"], inp["category"], inp["description"], inp["content_type"], inp["reasoning"]),
    }


def add_checklist_task(tenant_id: str, checklist_id: str, label: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.hrm_svc_base_url, timeout=10.0) as client:
            resp = client.post(f"/api/v1/hrm/checklists/{checklist_id}/tasks", json={"label": label}, headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_checklist_tasks(tasks: list[str], reasoning: str) -> dict[str, Any]:
    return {"tasks": tasks, "reasoning": reasoning}


CHECKLIST_TASK_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "submit_checklist_tasks",
        "description": (
            "Report the list of onboarding/offboarding tasks that apply to this employee given their role, "
            "department, and entity, and your reasoning. Always call this to conclude."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {"type": "array", "items": {"type": "string"}},
                "reasoning": {"type": "string"},
            },
            "required": ["tasks", "reasoning"],
        },
    },
]


def build_checklist_task_tool_dispatch(tenant_id: str) -> dict:
    return {
        "submit_checklist_tasks": lambda inp: submit_checklist_tasks(inp["tasks"], inp["reasoning"]),
    }
