"""AI-suggested audience segments for the marketing console.

Reads what marketing knows about its contacts (the attributes and their
values) and the segments that already exist, proposes audiences worth a
campaign — with Claude when the tenant has set up Anthropic (super admin
→ Integrations), with deterministic rules otherwise — and keeps only the
ones that match at least one contact and don't duplicate a segment.
Nothing is created: the marketer chooses."""
import json
import re
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.anthropic_client import AnthropicAgentClient
from src.core.config import get_settings
from src.models import AgentStatus, DecisionLog
from src.services import marketing_content_agent

AGENT_KEY = marketing_content_agent.AGENT_KEY
SYSTEM_PROMPT = """You are the Marketing Content Agent for VOYQI Mobility, suggesting audience \
segments worth a campaign. You get the contact attributes that exist (with their values) and the \
segments already defined. Reply with JSON only: {"suggestions": [{"name": str, "why": str, \
"filter_criteria": {attribute: value | [values] | {"min": n, "max": n}}, "channel": "email"|"sms"|"push"|"voice"}]} \
— at most 5, using only the attributes given, never duplicating an existing segment, each with a \
one-sentence reason a marketer would act on."""


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rule_suggestions(attributes: dict[str, list]) -> list[dict[str, Any]]:
    """The deterministic proposals — what a careful marketer would try first."""
    numeric = {k: [n for n in map(_num, v) if n is not None] for k, v in attributes.items()}
    out: list[dict[str, Any]] = []

    def add(name, why, criteria, channel="email"):
        out.append({"name": name, "why": why, "filter_criteria": criteria, "channel": channel})

    if numeric.get("churn_risk"):
        add("At churn risk", "Churn risk of 50 or more — a retention message now costs less than a win-back later.", {"churn_risk": {"min": 50}})
    if "upgrade_ready" in attributes:
        add("Ready to upgrade", "Members the Member Experience Agent found booking above their class.", {"upgrade_ready": ["true", "True", "yes", "1"]})
    if numeric.get("tenure_months") and numeric.get("churn_risk"):
        add("Loyal and happy", "A year or more in, low churn risk — the best audience for referrals and reviews.",
            {"tenure_months": {"min": 12}, "churn_risk": {"max": 30}})
    elif numeric.get("tenure_months"):
        add("New members (first 3 months)", "Early tenure is when habits form — onboarding tips land best here.", {"tenure_months": {"max": 3}})
    if numeric.get("last_trip_days"):
        add("Inactive for 14+ days", "No trip in two weeks — a nudge before inactivity becomes cancellation.", {"last_trip_days": {"min": 14}}, "push")
    if numeric.get("gu_balance"):
        add("High GU balance", "Members sitting on 1,000+ Growth Units — remind them what they can redeem.", {"gu_balance": {"min": 1000}})
    for key in ("city", "hub"):
        values = attributes.get(key) or []
        if 1 < len(values) <= 20:
            add(f"{values[0]} {('members' if key == 'city' else 'hub members')}", f"Local offers convert better than national ones — start with {values[0]}.", {key: values[0]})
            break
    if "class" in attributes:
        for premium in ("luxury_plus", "luxury", "performance_plus", "performance"):
            if premium in [str(v).lower() for v in attributes["class"]]:
                add(f"{premium.replace('_', ' ').title()} class", "Premium classes respond to exclusive, invitation-style offers.", {"class": premium})
                break
    return out


def _parse(text: str) -> list[dict[str, Any]]:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        return []
    good = []
    for s in data.get("suggestions") or []:
        if isinstance(s, dict) and isinstance(s.get("filter_criteria"), dict) and s.get("name"):
            good.append({"name": str(s["name"])[:120], "why": str(s.get("why") or "")[:400], "filter_criteria": s["filter_criteria"],
                         "channel": s.get("channel") if s.get("channel") in ("email", "sms", "push", "voice") else "email"})
    return good[:5]


def _marketing(tenant_id: str, method: str, path: str, body: dict | None = None):
    settings = get_settings()
    with httpx.Client(base_url=settings.marketing_svc_base_url, timeout=15.0) as c:
        r = c.request(method, f"/api/v1/marketing{path}", json=body, headers={"X-Tenant-Id": tenant_id, "X-AI-Initiated": AGENT_KEY})
    r.raise_for_status()
    return r.json()


async def suggest(db: AsyncSession, tenant_id: str, client: AnthropicAgentClient | None = None) -> dict[str, Any]:
    if await marketing_content_agent._agent_status(db, tenant_id) != AgentStatus.ACTIVE:
        return {"suggestions": [], "source": None, "skipped_reason": "agent not active"}
    try:
        attributes = _marketing(tenant_id, "GET", "/audience/attributes")
        existing = _marketing(tenant_id, "GET", "/audience/segments")
    except httpx.HTTPError as exc:
        return {"suggestions": [], "source": None, "skipped_reason": f"marketing unreachable ({type(exc).__name__})"}
    client = client or AnthropicAgentClient()
    source, proposals = "rules", []
    if client.is_configured and attributes:
        try:
            user = json.dumps({"attributes": attributes, "existing_segments": [{"name": s["name"], "filter_criteria": s["filter_criteria"]} for s in existing]})
            proposals = _parse(client.complete(SYSTEM_PROMPT, user, max_tokens=900))
            source = "claude" if proposals else "rules"
        except Exception:  # noqa: BLE001 — an API failure falls back to the rules
            proposals = []
    if not proposals:
        proposals = rule_suggestions(attributes)
    taken = [s["filter_criteria"] for s in existing]
    kept = []
    for p in proposals:
        if p["filter_criteria"] in taken:
            continue
        try:
            est = _marketing(tenant_id, "POST", "/audience/estimate", {"filter_criteria": p["filter_criteria"]})
        except httpx.HTTPError:
            continue  # criteria marketing can't evaluate: not worth offering
        if est.get("total"):
            kept.append({**p, "estimate": est})
    db.add(DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="marketing.segment_suggestions_requested",
        trigger_context={"attributes": sorted(attributes), "existing_segments": len(existing)},
        reasoning=(f"Suggested {len(kept)} segment(s) with Claude." if source == "claude" else
                   f"[deterministic rules] Suggested {len(kept)} segment(s) from the contact attributes available."),
        tools_called=[{"tool": "marketing.audience.attributes"}, {"tool": "marketing.audience.estimate", "count": len(proposals)}],
        action_taken="segments_suggested", outcome="success",
    ))
    await db.commit()
    return {"suggestions": kept, "source": source, "skipped_reason": None}
