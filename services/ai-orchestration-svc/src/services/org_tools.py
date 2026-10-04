"""
Origination tool adapters — used by the Revenue Agent's ORG-001
pre-qualification capability. Distinct module from crm_tools.py since
this is a genuinely different domain (the unified origination pipeline,
not CRM identity/leads), even though it's the same agent.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": settings.ai_initiated_header_value}


def set_case_prequalification(tenant_id: str, case_id: str, verdict: str, reasoning: str, next_step_guidance: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.org_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/org/cases/{case_id}/prequalification",
                json={"verdict": verdict, "reasoning": reasoning, "next_step_guidance": next_step_guidance},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def apply_case_risk_score(tenant_id: str, decision_id: str, risk_score: float, reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.org_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/org/decisions/{decision_id}/apply-score",
                json={"risk_score": risk_score, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_risk_score(risk_score: float, reasoning: str) -> dict[str, Any]:
    return {"risk_score": risk_score, "reasoning": reasoning}


RISK_SCORING_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_lead_score",
        "description": "Fetch the originating CRM-002 lead's score and routing, if this case has a linked lead reference.",
        "input_schema": {
            "type": "object",
            "properties": {"external_ref_id": {"type": "string"}},
            "required": ["external_ref_id"],
        },
    },
    {
        "name": "submit_risk_score",
        "description": (
            "Report your risk score 0.0-1.0 (higher = lower risk, more likely to approve) and your reasoning. "
            "Always call this to conclude."
        ),
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


def build_risk_scoring_tool_dispatch(tenant_id: str) -> dict:
    from src.services import crm_tools

    return {
        "get_lead_score": lambda inp: crm_tools.get_lead_score(tenant_id, inp["external_ref_id"]),
        "submit_risk_score": lambda inp: submit_risk_score(inp["risk_score"], inp["reasoning"]),
    }


def get_disparate_impact_snapshot(tenant_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.org_svc_base_url, timeout=10.0) as client:
            resp = client.get("/api/v1/org/compliance/disparate-impact-snapshot", headers=_headers(tenant_id))
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {"groups": []}


DISPARATE_IMPACT_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_disparate_impact_snapshot",
        "description": "Fetch current approval/decline rates grouped by origination type (business variables only, no protected-class data exists in this system).",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def build_disparate_impact_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_disparate_impact_snapshot": lambda inp: get_disparate_impact_snapshot(tenant_id),
    }


def get_funding_sources(tenant_id: str, origination_type: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.org_svc_base_url, timeout=10.0) as client:
            resp = client.get("/api/v1/org/funding-sources", params={"origination_type": origination_type}, headers=_headers(tenant_id))
            resp.raise_for_status()
            return {"sources": resp.json()}
    except httpx.HTTPError:
        return {"sources": []}


def submit_funding_match(candidate_funding_source_id: str | None, reasoning: str) -> dict[str, Any]:
    return {"candidate_funding_source_id": candidate_funding_source_id, "reasoning": reasoning}


FUNDING_MATCH_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_funding_sources",
        "description": "Fetch every active funding source in the panel for this origination type (program guidelines: min risk score, max amount, reserve participation).",
        "input_schema": {
            "type": "object",
            "properties": {"origination_type": {"type": "string"}},
            "required": ["origination_type"],
        },
    },
    {
        "name": "submit_funding_match",
        "description": (
            "Report which funding source (by id) is the best match, or null if none qualifies, with your reasoning. "
            "Always call this to conclude."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate_funding_source_id": {"type": ["string", "null"]},
                "reasoning": {"type": "string"},
            },
            "required": ["candidate_funding_source_id", "reasoning"],
        },
    },
]


def build_funding_match_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_funding_sources": lambda inp: get_funding_sources(tenant_id, inp["origination_type"]),
        "submit_funding_match": lambda inp: submit_funding_match(inp.get("candidate_funding_source_id"), inp["reasoning"]),
    }


def submit_prequalification(verdict: str, next_step_guidance: str, reasoning: str) -> dict[str, Any]:
    return {"verdict": verdict, "next_step_guidance": next_step_guidance, "reasoning": reasoning}


PREQUALIFICATION_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_lead_score",
        "description": "Fetch the originating CRM-002 lead's score and routing, if this case has a linked lead reference.",
        "input_schema": {
            "type": "object",
            "properties": {"external_ref_id": {"type": "string"}},
            "required": ["external_ref_id"],
        },
    },
    {
        "name": "submit_prequalification",
        "description": (
            "Report your pre-qualification verdict (e.g. 'qualified', 'needs_review', 'incomplete'), the "
            "recommended next step, and your reasoning. Always call this to conclude."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string"},
                "next_step_guidance": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["verdict", "next_step_guidance", "reasoning"],
        },
    },
]


def build_prequalification_tool_dispatch(tenant_id: str) -> dict:
    from src.services import crm_tools

    return {
        "get_lead_score": lambda inp: crm_tools.get_lead_score(tenant_id, inp["external_ref_id"]),
        "submit_prequalification": lambda inp: submit_prequalification(inp["verdict"], inp["next_step_guidance"], inp["reasoning"]),
    }
