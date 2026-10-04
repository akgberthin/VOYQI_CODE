"""
Revenue Agent — CRM-002 lead scoring/routing, and CRM-001 identity
resolution. Both are "is this the same person / is this worth pursuing"
judgment calls, so one agent covers both rather than inventing a
second — the same reuse principle as the Pricing Intelligence Agent
covering four different valuation surfaces.

AI-first, stated concretely: evaluate_identity_resolution runs
automatically the instant crm-svc publishes crm.source_record_created —
not on request. Two-layer discipline throughout:

1. Deterministic fallback — exact email/phone/name matching against
   every existing profile. Real, reliable, and honest about its limits:
   it will not catch "Bob Smith" vs "Robert Smith, Jr." the way a live
   reasoning pass might.
2. Live Claude path — reads the same real profile data via tool use,
   catches nuance the deterministic pass can't, but is checked against
   nothing lower a stricter floor: it must still explicitly report a
   confidence score, and a MatchCandidate is only ever a suggestion —
   confirm_merge in crm-svc is the sole human-confirmed action that
   actually changes the identity graph.
"""
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.anthropic_client import AnthropicAgentClient
from src.core.config import get_settings
from src.models import Agent, AgentStatus, DecisionLog
from src.services import crm_tools, org_tools

AGENT_KEY = "revenue_agent"

IDENTITY_RESOLUTION_SYSTEM_PROMPT = """You are the Revenue Agent for VOYQI's Enterprise CRM, \
evaluating whether a newly-arrived record belongs to an existing customer.

Use get_person_profiles to see every existing resolved identity (name, email, phone) \
in this tenant. Compare the new record against each one — exact email or phone matches \
are strong signals, but also use judgment for near-matches a strict string comparison \
would miss (nicknames, middle names, minor typos, "Jr."/"Sr." suffixes). If no profile \
is a good match, say so plainly — call submit_match_result with \
candidate_person_profile_id=null rather than forcing a weak match. A false merge is \
worse than an unresolved record: it silently corrupts a person's history. Always finish \
by calling submit_match_result."""


async def _agent_status(db: AsyncSession, tenant_id: str) -> AgentStatus:
    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    return agent_row.status if agent_row else AgentStatus.ACTIVE


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _deterministic_match(full_name: str, email: str, phone: str, profiles: list[dict]) -> tuple[str | None, float, str]:
    best_id: str | None = None
    best_score = 0.0
    best_reason = ""
    norm_phone = _normalize_phone(phone)

    for p in profiles:
        score = 0.0
        reason = ""
        if email and p.get("primary_email") and email.strip().lower() == p["primary_email"].strip().lower():
            score, reason = 0.95, f"exact email match ({email})"
        elif norm_phone and p.get("primary_phone") and norm_phone == _normalize_phone(p.get("primary_phone", "")):
            score, reason = 0.85, f"exact phone match ({phone})"
        elif full_name and p.get("full_name") and full_name.strip().lower() == p["full_name"].strip().lower():
            score, reason = 0.6, f"exact full name match ({full_name}) — weaker signal, no email/phone confirmation"

        if score > best_score:
            best_score, best_id, best_reason = score, p["id"], reason

    return best_id, best_score, best_reason


@dataclass
class IdentityResolutionResult:
    decision_log: DecisionLog
    candidate_person_profile_id: str | None
    match_score: float
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_identity_resolution(
    db: AsyncSession,
    tenant_id: str,
    source_record_id: str,
    full_name: str,
    email: str,
    phone: str,
    client: AnthropicAgentClient | None = None,
) -> IdentityResolutionResult:
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="crm.source_record_created",
            trigger_context={"source_record_id": source_record_id, "full_name": full_name},
            reasoning=f"Agent status is {status.value} — no identity resolution was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return IdentityResolutionResult(decision_log=log, candidate_person_profile_id=None, match_score=0.0, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    settings = get_settings()
    tools_called: list[dict] = []
    candidate_id: str | None = None
    match_score = 0.0

    if client.is_configured:
        dispatch = crm_tools.build_identity_resolution_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=IDENTITY_RESOLUTION_SYSTEM_PROMPT,
            user_message=f"New record: name='{full_name}', email='{email}', phone='{phone}'. Find the best-matching existing profile, if any.",
            tools=crm_tools.IDENTITY_RESOLUTION_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_match_result"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            candidate_id = submitted.get("candidate_person_profile_id")
            match_score = submitted.get("match_score", 0.0)
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            reasoning = turn.final_text or "(agent produced no final text and never called submit_match_result)"
    else:
        profiles_response = crm_tools.get_person_profiles(tenant_id)
        profiles = profiles_response.get("profiles", [])
        tools_called = [{"tool": "get_person_profiles", "input": {}}]
        candidate_id, match_score, match_reason = _deterministic_match(full_name, email, phone, profiles)
        if candidate_id and match_score >= settings.duplicate_match_score_threshold:
            reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {match_reason}."
        else:
            candidate_id = None
            reasoning = (
                "[deterministic fallback — ANTHROPIC_API_KEY not configured] No existing profile matched on exact "
                f"email or phone, or the only signal found (name-only, threshold {settings.duplicate_match_score_threshold:.2f}) "
                "was too weak to flag automatically."
            )

    if candidate_id:
        crm_tools.create_match_candidate(tenant_id, source_record_id, candidate_id, match_score, reasoning)
        tools_called.append({"tool": "create_match_candidate", "input": {"source_record_id": source_record_id, "candidate_person_profile_id": candidate_id}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="crm.source_record_created",
        trigger_context={"source_record_id": source_record_id, "full_name": full_name, "email": email, "phone": phone},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="match_candidate_created" if candidate_id else "no_match_found",
        outcome="success" if candidate_id else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return IdentityResolutionResult(decision_log=log, candidate_person_profile_id=candidate_id, match_score=match_score, reasoning=reasoning)


async def handle_source_record_created(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> IdentityResolutionResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_identity_resolution(
        db, tenant_id, payload.get("source_record_id", ""), payload.get("full_name", ""),
        payload.get("email", ""), payload.get("phone", ""),
    )


LEAD_SCORING_SYSTEM_PROMPT = """You are the Revenue Agent for VOYQI's Enterprise CRM, \
scoring and routing a freshly-captured lead (CRM-002).

First call check_duplicate_leads with the lead's email/phone — scoring a lead that's \
actually a duplicate of one already in the pipeline wastes a sales rep's time chasing \
the same person twice. If real duplicates exist, factor that into your reasoning. Then \
score the lead 0.0-1.0 based on the signals actually available (contact-info \
completeness, email domain professionalism, intake channel, and anything else \
genuinely present) and recommend which team it should route to: enterprise_sales \
(B2B/fleet-looking leads), retail_sales (dealership-channel leads), member_experience \
(subscription/rental-looking leads), or general_sales as a fallback. Always finish by \
calling submit_lead_score."""

_FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com"}
_SOURCE_WEIGHT = {"dealer": 0.3, "hub": 0.25, "app": 0.2, "web_landing_page": 0.15}
_SOURCE_TEAM = {"dealer": "retail_sales", "hub": "member_experience", "app": "member_experience", "web_landing_page": "general_sales"}


def _deterministic_lead_score(source: str, email: str, phone: str) -> tuple[float, str, str]:
    score = 0.1
    reasons = []

    if email and phone:
        score += 0.3
        reasons.append("both email and phone provided (complete contact info)")

    domain = email.split("@")[-1].lower() if "@" in email else ""
    is_corporate_domain = bool(domain) and domain not in _FREE_EMAIL_DOMAINS
    if is_corporate_domain:
        score += 0.3
        reasons.append(f"email domain '{domain}' is not a common free consumer provider")

    source_weight = _SOURCE_WEIGHT.get(source, 0.1)
    score += source_weight
    reasons.append(f"intake channel '{source}' weight {source_weight:.2f}")

    score = min(score, 1.0)
    team = "enterprise_sales" if is_corporate_domain and score >= 0.6 else _SOURCE_TEAM.get(source, "general_sales")
    reasoning = "; ".join(reasons)
    return score, team, reasoning


@dataclass
class LeadScoringResult:
    decision_log: DecisionLog
    lead_id: str
    score: float | None
    routed_to_team: str
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_lead_scoring(
    db: AsyncSession,
    tenant_id: str,
    lead_id: str,
    source: str,
    full_name: str,
    email: str,
    phone: str,
    client: AnthropicAgentClient | None = None,
) -> LeadScoringResult:
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="lead.captured",
            trigger_context={"lead_id": lead_id, "full_name": full_name},
            reasoning=f"Agent status is {status.value} — no lead scoring was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return LeadScoringResult(decision_log=log, lead_id=lead_id, score=None, routed_to_team="", reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = crm_tools.build_lead_scoring_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=LEAD_SCORING_SYSTEM_PROMPT,
            user_message=f"New lead: name='{full_name}', email='{email}', phone='{phone}', source='{source}'. Score and route it.",
            tools=crm_tools.LEAD_SCORING_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_lead_score"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            score = submitted.get("score", 0.0)
            team = submitted.get("routed_to_team", "general_sales")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            score, team = 0.0, "general_sales"
            reasoning = turn.final_text or "(agent produced no final text and never called submit_lead_score)"
    else:
        score, team, det_reason = _deterministic_lead_score(source, email, phone)
        tools_called = [{"tool": "check_duplicate_leads", "input": {"email": email, "phone": phone}}]
        dupes = crm_tools.check_duplicate_leads(tenant_id, email, phone)
        dupe_note = f" {len(dupes.get('duplicates', []))} potential duplicate(s) found." if dupes.get("duplicates") else " No duplicates found."
        reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {det_reason}.{dupe_note}"

    crm_tools.update_lead_score(tenant_id, lead_id, score, reasoning, team)
    tools_called.append({"tool": "update_lead_score", "input": {"lead_id": lead_id, "score": score, "routed_to_team": team}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="lead.captured",
        trigger_context={"lead_id": lead_id, "full_name": full_name, "email": email, "phone": phone, "source": source},
        reasoning=reasoning, tools_called=tools_called, action_taken="lead_scored_and_routed", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return LeadScoringResult(decision_log=log, lead_id=lead_id, score=score, routed_to_team=team, reasoning=reasoning)


async def handle_lead_captured(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> LeadScoringResult:
    """Adapter matching the supervisor's AgentHandler signature for
    lead.captured. This creates the crm-svc Lead record itself (the
    unified CRM-002 pipeline entry) before scoring it — marketing-svc's
    LeadCapture is the raw landing-page-side record; this is where it
    becomes a real CRM lead spanning every intake channel."""
    lead_response = crm_tools.create_lead(
        tenant_id, "web_landing_page", payload.get("name", ""), payload.get("email", ""), payload.get("phone", ""),
        external_ref_id=payload.get("lead_id", ""),
    )
    lead_id = lead_response.get("id", "")
    return await evaluate_lead_scoring(
        db, tenant_id, lead_id, "web_landing_page", payload.get("name", ""), payload.get("email", ""), payload.get("phone", ""),
    )


PROFILE_DEDUP_SYSTEM_PROMPT = """You are the Revenue Agent for VOYQI's Enterprise CRM, \
checking whether a newly-created person profile is actually a duplicate of one that \
already exists (CRM-007) — distinct from CRM-001's identity resolution, which only \
catches duplicates arriving through a new source record. This profile was created \
directly, so it needs its own check against every other existing profile.

Use get_active_person_profiles to see every other resolved identity (name, email, \
phone) in this tenant. Apply the same judgment as identity resolution — exact email or \
phone matches are strong signals, but also catch near-matches a strict string \
comparison would miss. If no profile is a good match, say so plainly — call \
submit_profile_match_result with candidate_profile_id=null rather than forcing a weak \
match. A false merge silently corrupts two people's histories together; an unresolved \
duplicate just sits there, waiting, which is the safer failure mode. Always finish by \
calling submit_profile_match_result."""


@dataclass
class ProfileDeduplicationResult:
    decision_log: DecisionLog
    candidate_profile_id: str | None
    match_score: float
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_profile_deduplication(
    db: AsyncSession,
    tenant_id: str,
    person_profile_id: str,
    full_name: str,
    email: str,
    phone: str,
    client: AnthropicAgentClient | None = None,
) -> ProfileDeduplicationResult:
    """CRM-007 — reuses the exact same deterministic fuzzy-match
    discipline as evaluate_identity_resolution, applied profile-to-
    profile instead of source-record-to-profile. Runs automatically the
    instant crm-svc publishes crm.person_profile_created."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="crm.person_profile_created",
            trigger_context={"person_profile_id": person_profile_id, "full_name": full_name},
            reasoning=f"Agent status is {status.value} — no profile deduplication check was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return ProfileDeduplicationResult(decision_log=log, candidate_profile_id=None, match_score=0.0, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    settings = get_settings()
    tools_called: list[dict] = []
    candidate_id: str | None = None
    match_score = 0.0

    if client.is_configured:
        dispatch = crm_tools.build_profile_dedup_tool_dispatch(tenant_id, person_profile_id)
        turn = client.run_agent_turn(
            system_prompt=PROFILE_DEDUP_SYSTEM_PROMPT,
            user_message=f"New profile: name='{full_name}', email='{email}', phone='{phone}'. Find the best-matching existing profile, if any.",
            tools=crm_tools.PROFILE_DEDUP_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_profile_match_result"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            candidate_id = submitted.get("candidate_profile_id")
            match_score = submitted.get("match_score", 0.0)
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            reasoning = turn.final_text or "(agent produced no final text and never called submit_profile_match_result)"
    else:
        profiles_response = crm_tools.get_active_person_profiles(tenant_id, person_profile_id)
        profiles = profiles_response.get("profiles", [])
        tools_called = [{"tool": "get_active_person_profiles", "input": {}}]
        candidate_id, match_score, match_reason = _deterministic_match(full_name, email, phone, profiles)
        if candidate_id and match_score >= settings.duplicate_match_score_threshold:
            reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {match_reason}."
        else:
            candidate_id = None
            reasoning = (
                "[deterministic fallback — ANTHROPIC_API_KEY not configured] No other existing profile matched on "
                f"exact email or phone, or the only signal found was too weak to flag automatically "
                f"(threshold {settings.duplicate_match_score_threshold:.2f})."
            )

    if candidate_id:
        crm_tools.create_profile_duplicate_candidate(tenant_id, candidate_id, person_profile_id, match_score, reasoning)
        tools_called.append({"tool": "create_profile_duplicate_candidate", "input": {"profile_a_id": candidate_id, "profile_b_id": person_profile_id}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="crm.person_profile_created",
        trigger_context={"person_profile_id": person_profile_id, "full_name": full_name, "email": email, "phone": phone},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="profile_duplicate_candidate_created" if candidate_id else "no_duplicate_found",
        outcome="success" if candidate_id else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return ProfileDeduplicationResult(decision_log=log, candidate_profile_id=candidate_id, match_score=match_score, reasoning=reasoning)


async def handle_person_profile_created(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> ProfileDeduplicationResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_profile_deduplication(
        db, tenant_id, payload.get("person_profile_id", ""), payload.get("full_name", ""), payload.get("email", ""), payload.get("phone", ""),
    )


PREQUALIFICATION_SYSTEM_PROMPT = """You are the Revenue Agent for VOYQI's Sales & \
Originations engine, pre-qualifying a newly-created origination case (ORG-001) —
retail finance, subscription enrollment, or rental-to-subscription conversion.

Use get_lead_score to check whether this case has a linked CRM-002 lead already \
scored — real prior signal worth weighing before pre-qualifying. Report a verdict \
(e.g. 'qualified', 'needs_review', 'incomplete'), a concrete next-step recommendation \
for the originations team, and your reasoning. This is pre-qualification only — a \
lighter viability signal, not a credit decision or final approval, which happens \
separately at ORG-002. Always finish by calling submit_prequalification."""


def _deterministic_prequalification(origination_type: str, external_ref_id: str, lead_response: dict) -> tuple[str, str, str]:
    lead = lead_response.get("lead")
    if lead and lead.get("score") is not None:
        score = lead["score"]
        if score >= 0.6:
            verdict, next_step = "qualified", "proceed to decisioning"
        else:
            verdict, next_step = "needs_review", "route to a human rep for manual review before proceeding"
        reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Linked CRM-002 lead score {score:.2f} (routed to {lead.get('routed_to_team', '(unassigned)')})."
    elif external_ref_id:
        verdict, next_step = "qualified", "proceed to decisioning"
        reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Case has a linked {origination_type} reference ({external_ref_id}) and complete contact info."
    else:
        verdict, next_step = "incomplete", "collect a linked case reference before proceeding"
        reasoning = "[deterministic fallback — ANTHROPIC_API_KEY not configured] No linked lead score or case reference found — insufficient signal to pre-qualify automatically."
    return verdict, next_step, reasoning


@dataclass
class PrequalificationResult:
    decision_log: DecisionLog
    verdict: str | None
    next_step_guidance: str
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_origination_prequalification(
    db: AsyncSession,
    tenant_id: str,
    case_id: str,
    origination_type: str,
    full_name: str,
    external_ref_id: str = "",
    client: AnthropicAgentClient | None = None,
) -> PrequalificationResult:
    """ORG-001 — the Revenue Agent's fourth capability. Runs
    automatically the instant org-svc publishes org.case_created."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="org.case_created",
            trigger_context={"case_id": case_id, "origination_type": origination_type},
            reasoning=f"Agent status is {status.value} — no pre-qualification was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return PrequalificationResult(decision_log=log, verdict=None, next_step_guidance="", reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = org_tools.build_prequalification_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=PREQUALIFICATION_SYSTEM_PROMPT,
            user_message=f"New {origination_type} case for {full_name}, linked reference: '{external_ref_id or '(none)'}'. Pre-qualify it.",
            tools=org_tools.PREQUALIFICATION_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_prequalification"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            verdict = submitted.get("verdict", "needs_review")
            next_step = submitted.get("next_step_guidance", "")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            verdict, next_step = "needs_review", ""
            reasoning = turn.final_text or "(agent produced no final text and never called submit_prequalification)"
    else:
        lead_response = crm_tools.get_lead_score(tenant_id, external_ref_id)
        tools_called = [{"tool": "get_lead_score", "input": {"external_ref_id": external_ref_id}}]
        verdict, next_step, reasoning = _deterministic_prequalification(origination_type, external_ref_id, lead_response)

    org_tools.set_case_prequalification(tenant_id, case_id, verdict, reasoning, next_step)
    tools_called.append({"tool": "set_case_prequalification", "input": {"case_id": case_id, "verdict": verdict}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="org.case_created",
        trigger_context={"case_id": case_id, "origination_type": origination_type, "full_name": full_name, "external_ref_id": external_ref_id},
        reasoning=reasoning, tools_called=tools_called, action_taken="prequalification_set", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return PrequalificationResult(decision_log=log, verdict=verdict, next_step_guidance=next_step, reasoning=reasoning)


async def handle_case_created(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> PrequalificationResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_origination_prequalification(
        db, tenant_id, payload.get("case_id", ""), payload.get("origination_type", ""), payload.get("full_name", ""), payload.get("external_ref_id", ""),
    )


RISK_SCORING_SYSTEM_PROMPT = """You are the Revenue Agent for VOYQI's Sales & \
Originations engine, risk-scoring a subscription enrollment or rental-to-subscription \
conversion case (ORG-002). Retail finance cases have their own dedicated credit \
decisioning in dms-svc and never reach you here.

Use get_lead_score to check for a linked CRM-002 lead score — real prior signal. Report \
a risk score from 0.0 to 1.0, where higher means lower risk (more likely to approve). \
You are scoring, not deciding: the actual approve/decline/manual-review outcome is a \
fixed threshold applied consistently to your score by the system, not something you \
determine — this keeps every case held to the same bar regardless of framing. Always \
finish by calling submit_risk_score."""


def _deterministic_risk_score(external_ref_id: str, lead_response: dict) -> tuple[float, str]:
    lead = lead_response.get("lead")
    if lead and lead.get("score") is not None:
        score = lead["score"]
        reasoning = (
            f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Using linked CRM-002 lead score "
            f"{score:.2f} directly as the risk score (routed to {lead.get('routed_to_team', '(unassigned)')})."
        )
        return score, reasoning
    if external_ref_id:
        reasoning = (
            "[deterministic fallback — ANTHROPIC_API_KEY not configured] No linked lead score found, but the case "
            f"has a real underlying reference ({external_ref_id}) — moderate score pending fuller signal."
        )
        return 0.5, reasoning
    reasoning = (
        "[deterministic fallback — ANTHROPIC_API_KEY not configured] No linked lead score or case reference — "
        "insufficient signal, defaulting to the manual-review midpoint rather than guessing."
    )
    return 0.5, reasoning


@dataclass
class RiskScoringResult:
    decision_log: DecisionLog
    decision_id: str
    risk_score: float | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_origination_decisioning(
    db: AsyncSession,
    tenant_id: str,
    decision_id: str,
    case_id: str,
    origination_type: str,
    full_name: str,
    external_ref_id: str = "",
    client: AnthropicAgentClient | None = None,
) -> RiskScoringResult:
    """ORG-002 — the Revenue Agent's fifth capability. Runs
    automatically the instant org-svc publishes
    org.decisioning_requested. Scores risk; the threshold decision
    itself is applied deterministically by org-svc's apply_risk_score,
    not by this function."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="org.decisioning_requested",
            trigger_context={"decision_id": decision_id, "case_id": case_id},
            reasoning=f"Agent status is {status.value} — no risk scoring was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return RiskScoringResult(decision_log=log, decision_id=decision_id, risk_score=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = org_tools.build_risk_scoring_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=RISK_SCORING_SYSTEM_PROMPT,
            user_message=f"{origination_type} case for {full_name}, linked reference: '{external_ref_id or '(none)'}'. Score its risk.",
            tools=org_tools.RISK_SCORING_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_risk_score"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            risk_score = submitted.get("risk_score", 0.5)
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            risk_score = 0.5
            reasoning = turn.final_text or "(agent produced no final text and never called submit_risk_score)"
    else:
        lead_response = crm_tools.get_lead_score(tenant_id, external_ref_id)
        tools_called = [{"tool": "get_lead_score", "input": {"external_ref_id": external_ref_id}}]
        risk_score, reasoning = _deterministic_risk_score(external_ref_id, lead_response)

    org_tools.apply_case_risk_score(tenant_id, decision_id, risk_score, reasoning)
    tools_called.append({"tool": "apply_case_risk_score", "input": {"decision_id": decision_id, "risk_score": risk_score}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="org.decisioning_requested",
        trigger_context={"decision_id": decision_id, "case_id": case_id, "origination_type": origination_type, "full_name": full_name},
        reasoning=reasoning, tools_called=tools_called, action_taken="risk_score_applied", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return RiskScoringResult(decision_log=log, decision_id=decision_id, risk_score=risk_score, reasoning=reasoning)


async def handle_decisioning_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> RiskScoringResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_origination_decisioning(
        db, tenant_id, payload.get("decision_id", ""), payload.get("case_id", ""), payload.get("origination_type", ""),
        payload.get("full_name", ""), payload.get("external_ref_id", ""),
    )


FUNDING_MATCH_SYSTEM_PROMPT = """You are the Revenue Agent for VOYQI's Sales & \
Originations engine, matching a decisioned case to the best funding source in the \
panel (ORG-003).

Use get_funding_sources to see every active funding source for this origination type — \
each has real program guidelines (minimum risk score, maximum amount, dealer reserve \
participation rate). Filter to sources that actually qualify (risk score at or above \
their minimum, amount within their maximum), then pick the best one — usually the \
highest dealer reserve among qualifying sources, but use judgment if there's a reason \
a lower-reserve source is actually the better fit (e.g. a captive finance arm the \
member relationship benefits from). If no source qualifies, say so plainly rather than \
forcing a bad match. Always finish by calling submit_funding_match."""


def _deterministic_funding_match(risk_score: float, amount_usd: float, origination_type: str, sources: list[dict]) -> tuple[str | None, str]:
    candidates = [
        s for s in sources
        if s["applicable_origination_type"] == origination_type
        and s["min_risk_score"] <= risk_score
        and s["max_amount_usd"] >= amount_usd
    ]
    if not candidates:
        return None, (
            f"[deterministic fallback — ANTHROPIC_API_KEY not configured] No active funding source qualifies for a "
            f"{origination_type} case with risk score {risk_score:.2f} and amount ${amount_usd:,.0f}."
        )
    best = max(candidates, key=lambda s: s["reserve_participation_pct"])
    return best["id"], (
        f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Best-qualifying funding source by dealer "
        f"reserve participation: {best['name']} ({best['reserve_participation_pct']:.1f}% reserve)."
    )


@dataclass
class FundingMatchResult:
    decision_log: DecisionLog
    candidate_funding_source_id: str | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_funding_source_matching(
    db: AsyncSession,
    tenant_id: str,
    case_id: str,
    origination_type: str,
    risk_score: float,
    amount_usd: float,
    client: AnthropicAgentClient | None = None,
) -> FundingMatchResult:
    """ORG-003 — the Revenue Agent's sixth capability. Runs
    automatically the instant org-svc publishes
    org.funding_match_requested, once a case has cleared decisioning
    with a real risk score."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="org.funding_match_requested",
            trigger_context={"case_id": case_id, "origination_type": origination_type},
            reasoning=f"Agent status is {status.value} — no funding match was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return FundingMatchResult(decision_log=log, candidate_funding_source_id=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = org_tools.build_funding_match_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=FUNDING_MATCH_SYSTEM_PROMPT,
            user_message=f"{origination_type} case, risk score {risk_score:.2f}, amount ${amount_usd:,.0f}. Find the best funding source match.",
            tools=org_tools.FUNDING_MATCH_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_funding_match"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            candidate_id = submitted.get("candidate_funding_source_id")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            candidate_id = None
            reasoning = turn.final_text or "(agent produced no final text and never called submit_funding_match)"
    else:
        sources_response = org_tools.get_funding_sources(tenant_id, origination_type)
        sources = sources_response.get("sources", [])
        tools_called = [{"tool": "get_funding_sources", "input": {"origination_type": origination_type}}]
        candidate_id, reasoning = _deterministic_funding_match(risk_score, amount_usd, origination_type, sources)

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="org.funding_match_requested",
        trigger_context={"case_id": case_id, "origination_type": origination_type, "risk_score": risk_score, "amount_usd": amount_usd},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="funding_match_found" if candidate_id else "no_funding_match_found",
        outcome="success" if candidate_id else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return FundingMatchResult(decision_log=log, candidate_funding_source_id=candidate_id, reasoning=reasoning)


async def handle_funding_match_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> FundingMatchResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_funding_source_matching(
        db, tenant_id, payload.get("case_id", ""), payload.get("origination_type", ""), payload.get("risk_score", 0.0), payload.get("amount_usd", 0.0),
    )
