"""
Real call into ai-orchestration-svc's Member Experience Agent. A member's
right to cancel is never blocked on this succeeding — if the agent is
unreachable, initiate_cancellation proceeds without a win-back offer
rather than failing the request.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def request_winback_evaluation(tenant_id: str, member_profile_id: str, subscription_id: str, reason: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=8.0) as client:
            resp = client.post(
                "/api/v1/ai/member-experience/evaluate-winback",
                json={"member_profile_id": member_profile_id, "subscription_id": subscription_id, "reason": reason},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_deposit_capture_review(tenant_id: str, hold_id: str, amount_usd: float, reason: str) -> dict[str, Any] | None:
    """Fired the instant a capture is requested — routes through the
    same generic /api/v1/ai/events endpoint the supervisor uses for
    every event type, landing on compliance_audit_agent.evaluate_event
    for deposit.capture_requested. Never blocks: the capture already
    sits in CAPTURE_PENDING_REVIEW regardless of whether this call
    succeeds — deposit_engine's own state machine is what actually
    guarantees the human approval gate, not this call."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=8.0) as client:
            resp = client.post(
                "/api/v1/ai/events",
                json={"event_type": "deposit.capture_requested", "payload": {"hold_id": hold_id, "amount_usd": amount_usd, "reason": reason}},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None


def request_upgrade_pattern_evaluation(tenant_id: str, member_profile_id: str, subscription_id: str, current_class: str) -> dict[str, Any] | None:
    """Fired after every above-class request is recorded. Never
    blocks the member's own request — if the agent is slow or
    unreachable, the signal is still safely logged in subscription-svc's
    own table for the next evaluation to pick up."""
    settings = get_settings()
    try:
        with httpx.Client(base_url=settings.ai_orchestration_svc_base_url, timeout=8.0) as client:
            resp = client.post(
                "/api/v1/ai/member-experience/evaluate-upgrade-pattern",
                json={"member_profile_id": member_profile_id, "subscription_id": subscription_id, "current_class": current_class},
                headers={"X-Tenant-Id": tenant_id},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError:
        return None
