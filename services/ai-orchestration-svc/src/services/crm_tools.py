"""
CRM tool adapters — used by the Revenue Agent.

get_person_profiles is the real data source for identity resolution:
every existing person profile in the tenant, for the agent (or its
deterministic fallback) to fuzzy-match a new source record against.
create_match_candidate is the only way a MatchCandidate row gets
written — a suggestion, never an automatic merge; crm-svc's
confirm_merge is a separate, human-confirmed action this tool never
touches.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": settings.ai_initiated_header_value}


def get_person_profiles(tenant_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.get("/api/v1/crm/person-profiles", headers=_headers(tenant_id))
            resp.raise_for_status()
            return {"profiles": resp.json()}
    except httpx.HTTPError:
        return {"profiles": []}


def create_match_candidate(tenant_id: str, source_record_id: str, candidate_person_profile_id: str, match_score: float, match_reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/crm/match-candidates",
                json={
                    "source_record_id": source_record_id, "candidate_person_profile_id": candidate_person_profile_id,
                    "match_score": match_score, "match_reasoning": match_reasoning,
                },
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_match_result(candidate_person_profile_id: str | None, match_score: float, reasoning: str) -> dict[str, Any]:
    """Structured-output discipline: the agent must explicitly report
    'no match' (candidate_person_profile_id=None) rather than leaving
    it ambiguous whether it looked and found nothing, or didn't look."""
    return {"candidate_person_profile_id": candidate_person_profile_id, "match_score": match_score, "reasoning": reasoning}


IDENTITY_RESOLUTION_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_person_profiles",
        "description": "Fetch every existing resolved person profile in this tenant (name, email, phone) to compare a new source record against.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_match_result",
        "description": (
            "Report the best-matching existing person profile id (or null if none is a good match), your confidence "
            "score 0.0-1.0, and your reasoning. Always call this to conclude — don't only describe it in prose."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate_person_profile_id": {"type": ["string", "null"]},
                "match_score": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["candidate_person_profile_id", "match_score", "reasoning"],
        },
    },
]


def build_identity_resolution_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_person_profiles": lambda inp: get_person_profiles(tenant_id),
        "submit_match_result": lambda inp: submit_match_result(inp.get("candidate_person_profile_id"), inp["match_score"], inp["reasoning"]),
    }


def create_lead(tenant_id: str, source: str, full_name: str, email: str = "", phone: str = "", external_ref_id: str = "") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/crm/leads",
                json={"source": source, "full_name": full_name, "email": email, "phone": phone, "external_ref_id": external_ref_id},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def check_duplicate_leads(tenant_id: str, email: str = "", phone: str = "") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.get("/api/v1/crm/leads/duplicates/check", params={"email": email, "phone": phone}, headers=_headers(tenant_id))
            resp.raise_for_status()
            return {"duplicates": resp.json()}
    except httpx.HTTPError:
        return {"duplicates": []}


def update_lead_score(tenant_id: str, lead_id: str, score: float, reasoning: str, routed_to_team: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/crm/leads/{lead_id}/score",
                json={"score": score, "reasoning": reasoning, "routed_to_team": routed_to_team},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_lead_score(score: float, routed_to_team: str, reasoning: str) -> dict[str, Any]:
    return {"score": score, "routed_to_team": routed_to_team, "reasoning": reasoning}


LEAD_SCORING_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "check_duplicate_leads",
        "description": "Check for existing leads with the same email or phone before scoring — avoids re-scoring the same person as a fresh lead.",
        "input_schema": {
            "type": "object",
            "properties": {"email": {"type": "string"}, "phone": {"type": "string"}},
        },
    },
    {
        "name": "submit_lead_score",
        "description": (
            "Report the lead's score (0.0-1.0), which team it should be routed to (e.g. 'enterprise_sales', "
            "'retail_sales', 'concierge'), and your reasoning. Always call this to conclude."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "routed_to_team": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["score", "routed_to_team", "reasoning"],
        },
    },
]


def build_lead_scoring_tool_dispatch(tenant_id: str) -> dict:
    return {
        "check_duplicate_leads": lambda inp: check_duplicate_leads(tenant_id, inp.get("email", ""), inp.get("phone", "")),
        "submit_lead_score": lambda inp: submit_lead_score(inp["score"], inp["routed_to_team"], inp["reasoning"]),
    }


def get_conversation_history(tenant_id: str, conversation_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.get(f"/api/v1/crm/conversations/{conversation_id}/messages", headers=_headers(tenant_id))
            resp.raise_for_status()
            return {"messages": resp.json()}
    except httpx.HTTPError:
        return {"messages": []}


def set_message_sentiment_intent(tenant_id: str, message_id: str, sentiment: str, intent: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/crm/messages/{message_id}/sentiment-intent", json={"sentiment": sentiment, "intent": intent}, headers=_headers(tenant_id)
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def create_draft_reply(tenant_id: str, conversation_id: str, in_reply_to_message_id: str, draft_body: str, reasoning: str = "") -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/crm/draft-replies",
                json={"conversation_id": conversation_id, "in_reply_to_message_id": in_reply_to_message_id, "draft_body": draft_body, "reasoning": reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def escalate_conversation(tenant_id: str, conversation_id: str, priority: str, routed_to_team: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                f"/api/v1/crm/conversations/{conversation_id}/escalate",
                json={"priority": priority, "routed_to_team": routed_to_team},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_message_assessment(sentiment: str, intent: str, escalate: bool, draft_reply: str, reasoning: str) -> dict[str, Any]:
    return {"sentiment": sentiment, "intent": intent, "escalate": escalate, "draft_reply": draft_reply, "reasoning": reasoning}


MESSAGE_ASSESSMENT_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_conversation_history",
        "description": "Fetch every prior message in this conversation, to draft a reply with real context rather than in isolation.",
        "input_schema": {
            "type": "object",
            "properties": {"conversation_id": {"type": "string"}},
            "required": ["conversation_id"],
        },
    },
    {
        "name": "submit_message_assessment",
        "description": (
            "Report the message's sentiment (positive/neutral/negative), intent (complaint/sales_inquiry/support_request/general), "
            "whether it needs human escalation, your drafted reply, and your reasoning. Always call this to conclude."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sentiment": {"type": "string"},
                "intent": {"type": "string"},
                "escalate": {"type": "boolean"},
                "draft_reply": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["sentiment", "intent", "escalate", "draft_reply", "reasoning"],
        },
    },
]


def build_message_assessment_tool_dispatch(tenant_id: str) -> dict:
    return {
        "get_conversation_history": lambda inp: get_conversation_history(tenant_id, inp["conversation_id"]),
        "submit_message_assessment": lambda inp: submit_message_assessment(
            inp["sentiment"], inp["intent"], inp["escalate"], inp["draft_reply"], inp["reasoning"]
        ),
    }


def get_active_person_profiles(tenant_id: str, exclude_profile_id: str = "") -> dict[str, Any]:
    """Same underlying data as get_person_profiles, but named
    distinctly for the profile-dedup capability's own tool schema —
    keeps DecisionLog.tools_called unambiguous about which capability
    made which call."""
    response = get_person_profiles(tenant_id)
    profiles = [p for p in response.get("profiles", []) if p.get("id") != exclude_profile_id]
    return {"profiles": profiles}


def create_profile_duplicate_candidate(tenant_id: str, profile_a_id: str, profile_b_id: str, match_score: float, match_reasoning: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.post(
                "/api/v1/crm/profile-duplicate-candidates",
                json={"profile_a_id": profile_a_id, "profile_b_id": profile_b_id, "match_score": match_score, "match_reasoning": match_reasoning},
                headers=_headers(tenant_id),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return {}


def submit_profile_match_result(candidate_profile_id: str | None, match_score: float, reasoning: str) -> dict[str, Any]:
    return {"candidate_profile_id": candidate_profile_id, "match_score": match_score, "reasoning": reasoning}


PROFILE_DEDUP_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_active_person_profiles",
        "description": "Fetch every other existing, non-merged person profile in this tenant (name, email, phone) to compare a newly-created profile against.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_profile_match_result",
        "description": (
            "Report the best-matching existing profile id (or null if none is a good match), your confidence "
            "score 0.0-1.0, and your reasoning. Always call this to conclude."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate_profile_id": {"type": ["string", "null"]},
                "match_score": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": ["candidate_profile_id", "match_score", "reasoning"],
        },
    },
]


def build_profile_dedup_tool_dispatch(tenant_id: str, exclude_profile_id: str) -> dict:
    return {
        "get_active_person_profiles": lambda inp: get_active_person_profiles(tenant_id, exclude_profile_id),
        "submit_profile_match_result": lambda inp: submit_profile_match_result(inp.get("candidate_profile_id"), inp["match_score"], inp["reasoning"]),
    }


def get_lead_score(tenant_id: str, external_ref_id: str) -> dict[str, Any]:
    """Best-effort lookup: an origination case's lead_id/external_ref
    may already have a Revenue Agent lead score on file from CRM-002 —
    real signal worth reading before pre-qualifying, when it exists."""
    if not external_ref_id:
        return {"lead": None}
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.crm_svc_base_url, timeout=10.0) as client:
            resp = client.get(f"/api/v1/crm/leads/{external_ref_id}", headers=_headers(tenant_id))
            resp.raise_for_status()
            return {"lead": resp.json()}
    except httpx.HTTPError:
        return {"lead": None}
