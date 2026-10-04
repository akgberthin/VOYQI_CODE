"""
Marketing tool adapters, mirroring rbac_tools.py / identity_tools.py:
real HTTP calls into marketing-svc, exposed as Anthropic tool schemas
for each marketing agent's own reasoning loop.

marketing-svc's own design is the source of truth for what's
autonomous and what isn't, not this module: content drafting runs
through a deterministic guardrail (brand-voice/compliance checks,
channel length limits) that auto-approves a clean draft in the same
call it's created — there is no mandatory human-review status every
draft passes through. A draft the guardrail holds (HELD_FOR_REVIEW)
is the only one that waits on a human. Everything downstream of an
approved draft — scheduling a send, executing it, publishing a social
post, concluding an A/B test — is likewise autonomous; these tools
exist so the agents calling them inherit that same autonomy rather
than re-implementing a review gate marketing-svc doesn't have.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def _headers(tenant_id: str) -> dict:
    settings = get_settings()
    return {"X-Tenant-Id": tenant_id, "X-AI-Initiated": settings.ai_initiated_header_value}


def get_campaign_context(tenant_id: str, campaign_id: str) -> dict[str, Any]:
    """Read-only: fetches the campaign's own record and its
    performance summary to date, so the agent can write copy that
    fits the campaign's actual channel and objective rather than
    guessing, and downstream agents can factor real results into a
    send/publish/A-B decision."""
    settings = get_settings()
    with httpx.Client(base_url=settings.marketing_svc_base_url, timeout=10.0) as client:
        campaigns_resp = client.get("/api/v1/marketing/campaigns", headers=_headers(tenant_id))
        campaigns_resp.raise_for_status()
        match = next((c for c in campaigns_resp.json() if c["id"] == campaign_id), None)
        if match is None:
            return {"error": f"campaign {campaign_id} not found"}

        summary_resp = client.get(f"/api/v1/marketing/analytics/campaigns/{campaign_id}/summary", headers=_headers(tenant_id))
        summary_resp.raise_for_status()
        return {"campaign": match, "performance_to_date": summary_resp.json()}


def create_content_draft(tenant_id: str, campaign_id: str, channel: str, body_text: str, subject_line: str, agent_key: str) -> dict[str, Any]:
    """Writes a drafted piece of content to marketing-svc.
    marketing-svc's own deterministic guardrail runs on every draft
    the instant it's created and auto-approves a clean one — this
    call does not itself hold anything for review; whether the
    resulting draft is APPROVED or HELD_FOR_REVIEW is entirely a
    function of what the guardrail actually finds, independent of
    which agent or system prompt produced the text."""
    settings = get_settings()
    with httpx.Client(base_url=settings.marketing_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            "/api/v1/marketing/content",
            json={"campaign_id": campaign_id, "channel": channel, "body_text": body_text, "subject_line": subject_line, "created_by": f"ai:{agent_key}"},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


def schedule_send(tenant_id: str, campaign_id: str, content_draft_id: str, channel: str, scheduled_at: str) -> dict[str, Any]:
    """Schedules an already-approved draft for send. marketing-svc
    itself refuses (409) a draft that isn't APPROVED — that check
    lives there, not here, so this tool has no way to bypass it
    either."""
    settings = get_settings()
    with httpx.Client(base_url=settings.marketing_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            "/api/v1/marketing/sends",
            json={"campaign_id": campaign_id, "content_draft_id": content_draft_id, "channel": channel, "scheduled_at": scheduled_at},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


def execute_send(tenant_id: str, send_id: str, recipient_contacts: list[str]) -> dict[str, Any]:
    """Executes a scheduled send. marketing-svc checks the
    suppression list for every recipient before counting any of them
    as sent — that is the one check this tool can never skip, because
    the tool has no parameter that could skip it."""
    settings = get_settings()
    with httpx.Client(base_url=settings.marketing_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            f"/api/v1/marketing/sends/{send_id}/execute",
            json={"recipient_contacts": recipient_contacts},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


def schedule_social_post(tenant_id: str, social_account_id: str, content_draft_id: str, scheduled_at: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.marketing_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            "/api/v1/marketing/social/posts",
            json={"social_account_id": social_account_id, "content_draft_id": content_draft_id, "scheduled_at": scheduled_at},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


def publish_social_post(tenant_id: str, post_id: str) -> dict[str, Any]:
    """Publishing itself is a pluggable call to each platform's real
    API on marketing-svc's side; this tool just triggers that
    transition once a post is scheduled and approved."""
    settings = get_settings()
    with httpx.Client(base_url=settings.marketing_svc_base_url, timeout=10.0) as client:
        resp = client.post(f"/api/v1/marketing/social/posts/{post_id}/publish", json={}, headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


def evaluate_ab_test(tenant_id: str, test_id: str) -> dict[str, Any]:
    """Runs marketing-svc's real two-proportion z-test and concludes
    the test with a winner in the same call, if and only if both arms
    have enough data and the result is statistically significant —
    this tool has no parameter that could force an early call."""
    settings = get_settings()
    with httpx.Client(base_url=settings.marketing_svc_base_url, timeout=10.0) as client:
        resp = client.post(f"/api/v1/marketing/ab-tests/{test_id}/evaluate", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


MARKETING_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_campaign_context",
        "description": (
            "Fetch a campaign's own record (name, channel, objective) and its performance "
            "summary to date from marketing-svc, so drafted copy fits the actual campaign "
            "and downstream decisions can factor in real results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"campaign_id": {"type": "string"}},
            "required": ["campaign_id"],
        },
    },
    {
        "name": "create_content_draft",
        "description": (
            "Write a drafted piece of content to marketing-svc for one campaign and one "
            "channel. marketing-svc's own deterministic guardrail runs immediately and "
            "auto-approves a clean draft in this same call — this tool cannot itself force "
            "or skip that outcome either way."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
                "channel": {"type": "string", "enum": ["email", "sms", "push", "social", "voice"]},
                "subject_line": {"type": "string"},
                "body_text": {"type": "string"},
            },
            "required": ["campaign_id", "channel", "body_text"],
        },
    },
    {
        "name": "schedule_send",
        "description": "Schedule an already-approved content draft for send on a given channel at a given time. Refused with a 409 if the draft hasn't cleared the guardrail or a human review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string"},
                "content_draft_id": {"type": "string"},
                "channel": {"type": "string", "enum": ["email", "sms", "push", "voice"]},
                "scheduled_at": {"type": "string", "description": "ISO-8601 timestamp"},
            },
            "required": ["campaign_id", "content_draft_id", "channel", "scheduled_at"],
        },
    },
    {
        "name": "execute_send",
        "description": "Execute a scheduled send against a list of recipient contacts. Every recipient is checked against the suppression list before being counted as sent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "send_id": {"type": "string"},
                "recipient_contacts": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["send_id", "recipient_contacts"],
        },
    },
    {
        "name": "schedule_social_post",
        "description": "Schedule an already-approved content draft as a social post on a connected account. Refused with a 409 if the draft hasn't cleared the guardrail.",
        "input_schema": {
            "type": "object",
            "properties": {
                "social_account_id": {"type": "string"},
                "content_draft_id": {"type": "string"},
                "scheduled_at": {"type": "string", "description": "ISO-8601 timestamp"},
            },
            "required": ["social_account_id", "content_draft_id", "scheduled_at"],
        },
    },
    {
        "name": "publish_social_post",
        "description": "Publish a scheduled social post now.",
        "input_schema": {
            "type": "object",
            "properties": {"post_id": {"type": "string"}},
            "required": ["post_id"],
        },
    },
    {
        "name": "evaluate_ab_test",
        "description": "Run the real statistical test against an A/B test's recorded results. Concludes with a winner only if both arms have enough data and the result is significant; otherwise reports insufficient data and changes nothing.",
        "input_schema": {
            "type": "object",
            "properties": {"test_id": {"type": "string"}},
            "required": ["test_id"],
        },
    },
]


def build_tool_dispatch(tenant_id: str, agent_key: str) -> dict:
    return {
        "get_campaign_context": lambda inp: get_campaign_context(tenant_id, inp["campaign_id"]),
        "create_content_draft": lambda inp: create_content_draft(
            tenant_id, inp["campaign_id"], inp["channel"], inp["body_text"], inp.get("subject_line", ""), agent_key
        ),
        "schedule_send": lambda inp: schedule_send(tenant_id, inp["campaign_id"], inp["content_draft_id"], inp["channel"], inp["scheduled_at"]),
        "execute_send": lambda inp: execute_send(tenant_id, inp["send_id"], inp["recipient_contacts"]),
        "schedule_social_post": lambda inp: schedule_social_post(tenant_id, inp["social_account_id"], inp["content_draft_id"], inp["scheduled_at"]),
        "publish_social_post": lambda inp: publish_social_post(tenant_id, inp["post_id"]),
        "evaluate_ab_test": lambda inp: evaluate_ab_test(tenant_id, inp["test_id"]),
    }
