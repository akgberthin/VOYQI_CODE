"""
Outbound calls from hrm-svc to ai-orchestration-svc — same pass-through
pattern used by every other service's ai_orchestration_tools.py.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def request_checklist_population(tenant_id: str, checklist_id: str, kind: str, job_title: str, department: str, entity_ref: str = "") -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-checklist-population",
                json={"checklist_id": checklist_id, "kind": kind, "job_title": job_title, "department": department, "entity_ref": entity_ref},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_benefits_recommendation(tenant_id: str, employee_id: str, job_title: str, department: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-benefits-recommendation",
                json={"employee_id": employee_id, "job_title": job_title, "department": department},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_screening(tenant_id: str, application_id: str, resume_summary: str, required_skills: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-screening",
                json={"application_id": application_id, "resume_summary": resume_summary, "required_skills": required_skills},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_nurture(tenant_id: str, candidate_id: str, candidate_name: str, reason: str = "", application_id: str = "") -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-nurture",
                json={"candidate_id": candidate_id, "candidate_name": candidate_name, "reason": reason, "application_id": application_id},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_feedback_draft(tenant_id: str, review_id: str, manager_notes: str, rating: float | None = None) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-feedback-draft",
                json={"review_id": review_id, "manager_notes": manager_notes, "rating": rating},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_nine_box(tenant_id: str, employee_id: str, review_cycle_id: str, rating: float | None = None, goal_completion_rate: float | None = None) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-nine-box",
                json={"employee_id": employee_id, "review_cycle_id": review_cycle_id, "rating": rating, "goal_completion_rate": goal_completion_rate},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_attrition_risk(tenant_id: str, employee_id: str, factors: dict) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-attrition-risk",
                json={"employee_id": employee_id, **factors},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_course_recommendation(tenant_id: str, employee_id: str, incomplete_mandatory_course_ids: list[str], incomplete_mandatory_course_titles: list[str]) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-course-recommendation",
                json={"employee_id": employee_id, "incomplete_mandatory_course_ids": incomplete_mandatory_course_ids, "incomplete_mandatory_course_titles": incomplete_mandatory_course_titles},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_policy_answer(tenant_id: str, case_id: str, question: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-policy-question",
                json={"case_id": case_id, "question": question},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_note_update(tenant_id: str, employee_id: str, context: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-note-update",
                json={"employee_id": employee_id, "context": context},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_email_draft(tenant_id: str, recipient_scope: str, prompt: str, recipient_employee_id: str | None = None) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-email-draft",
                json={"recipient_scope": recipient_scope, "prompt": prompt, "recipient_employee_id": recipient_employee_id},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_course_draft(tenant_id: str, prompt: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/ai/hrm/evaluate-course-draft",
                json={"prompt": prompt},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None
