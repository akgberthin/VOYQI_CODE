"""
Talent Agent — the AI-native people-domain agent covering HRM-001
through HRM-007, mirroring how the Revenue Agent covers six
capabilities across CRM and Sales & Originations rather than spawning
a narrow agent per capability. "Candidates and employees are managed
with the same relationship-graph discipline as customers" (per the
HRM spec itself) — this agent is that discipline's AI layer.

This first capability: checklist task recommendation for onboarding
and offboarding (HRM-001). AI-first — runs automatically the instant
hrm-svc publishes hrm.employee_created or hrm.offboarding_initiated,
not on request. Two-layer discipline throughout:

1. Deterministic fallback — a real, defensible task template by
   department, not a placeholder. Every employee gets the same base
   tasks (paperwork, IT provisioning, equipment) plus department-
   specific ones (DMS access for dealership staff, hub network access
   and a VOYQI Card for mobility-ops/hub staff).
2. Live Claude path — reasons about nuance the template can't (a
   dealership employee who's also doing hub coverage, a role that
   needs something the template doesn't anticipate).

Either way, the checklist's own hard gate (mark_checklist_complete in
hrm-svc) still enforces that every task — however it got added — is
actually done before the checklist can close. The agent recommends
tasks; it never marks anything complete itself.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import action_gate
from src.core.anthropic_client import AnthropicAgentClient
from src.models import Agent, AgentStatus, DecisionLog
from src.services import hrm_tools

# Eight domain agents, one per HRM sub-module, each independently
# activatable/pausable and each with its own decision-log trail. This
# replaces the single "talent_agent" identity — pausing benefits
# automation no longer pauses recruiting, communication, etc.
AGENT_KEY_ONBOARDING = "onboarding_agent"                  # HRM-001
AGENT_KEY_BENEFITS = "benefits_agent"                      # HRM-002
AGENT_KEY_RECRUITING = "recruiting_agent"                  # HRM-003
AGENT_KEY_PERFORMANCE = "performance_agent"                # HRM-004
AGENT_KEY_WORKFORCE_ANALYTICS = "workforce_analytics_agent"  # HRM-005
AGENT_KEY_ACADEMY = "academy_agent"                        # HRM-006
AGENT_KEY_CASE_MANAGEMENT = "case_management_agent"        # HRM-007
AGENT_KEY_COMMUNICATION = "communication_agent"            # HRM-008

# Every HRM agent's actions live under one tier-policy namespace. Drafts,
# scores and proposals are tier 1; anything that reaches a person or
# changes their record is tier 2 until AI-004 data justifies promotion
# (docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §3, §5).
TALENT_POLICY_AGENT = "talent-workforce-intelligence"


async def _hrm_gate(db: AsyncSession, tenant_id: str, agent_key: str, action_type: str, executor: str, args: dict, reasoning: str, trigger: str) -> action_gate.GateResult:
    return await action_gate.gate(
        db, tenant_id=tenant_id, agent_key=agent_key, policy_agent=TALENT_POLICY_AGENT, action_type=action_type,
        executor=executor, args=args, reasoning=reasoning, triggering_event_type=trigger, domain="hrm",
    )

ALL_AGENT_KEYS = [
    AGENT_KEY_ONBOARDING, AGENT_KEY_BENEFITS, AGENT_KEY_RECRUITING, AGENT_KEY_PERFORMANCE,
    AGENT_KEY_WORKFORCE_ANALYTICS, AGENT_KEY_ACADEMY, AGENT_KEY_CASE_MANAGEMENT, AGENT_KEY_COMMUNICATION,
]

CHECKLIST_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, \
recommending which onboarding or offboarding checklist tasks apply to an employee \
given their role, department, and entity (HRM-001).

Onboarding tasks should cover paperwork, IT/system provisioning, and equipment at \
minimum, plus anything specific to the role (dealership system access, hub network \
access, a VOYQI Card, safety training for a shop-floor role, and so on). Offboarding \
tasks should mirror that in reverse — access revocation, equipment return, an exit \
interview — plus anything specific to what this role had access to. Don't recommend \
generic filler; every task should be something a real HR coordinator would actually \
need to do for this specific employee. Always finish by calling submit_checklist_tasks."""

_BASE_ONBOARDING_TASKS = ["Complete new hire paperwork", "Provision IT account", "Assign equipment"]
_BASE_OFFBOARDING_TASKS = ["Revoke IT account access", "Collect assigned equipment", "Conduct exit interview"]

_DEPARTMENT_ONBOARDING_TASKS = {
    "dealership": ["Provision DMS system access", "Issue rooftop badge/access"],
    "hub": ["Provision hub network access", "Issue VOYQI Card"],
    "mobility ops": ["Provision hub network access", "Issue VOYQI Card"],
    "service": ["Provision DMS system access", "Assign shop-floor safety training"],
}
_DEPARTMENT_OFFBOARDING_TASKS = {
    "dealership": ["Revoke DMS system access"],
    "hub": ["Revoke hub network access", "Deactivate VOYQI Card"],
    "mobility ops": ["Revoke hub network access", "Deactivate VOYQI Card"],
    "service": ["Revoke DMS system access"],
}


async def _agent_status(db: AsyncSession, tenant_id: str, agent_key: str) -> AgentStatus:
    result = await db.execute(select(Agent).where(Agent.key == agent_key, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    return agent_row.status if agent_row else AgentStatus.ACTIVE


def _deterministic_checklist_tasks(kind: str, department: str) -> tuple[list[str], str]:
    dept_key = department.strip().lower()
    if kind == "onboarding":
        base = list(_BASE_ONBOARDING_TASKS)
        dept_tasks = _DEPARTMENT_ONBOARDING_TASKS.get(dept_key, [])
    else:
        base = list(_BASE_OFFBOARDING_TASKS)
        dept_tasks = _DEPARTMENT_OFFBOARDING_TASKS.get(dept_key, [])

    tasks = base + dept_tasks
    if dept_tasks:
        reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Base {kind} tasks plus department-specific tasks for '{department}'."
    else:
        reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Base {kind} tasks only — no department-specific template matched '{department}'."
    return tasks, reasoning


@dataclass
class ChecklistPopulationResult:
    decision_log: DecisionLog
    tasks_added: list[str]
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_checklist_population(
    db: AsyncSession,
    tenant_id: str,
    checklist_id: str,
    kind: str,
    job_title: str,
    department: str,
    entity_ref: str = "",
    client: AnthropicAgentClient | None = None,
) -> ChecklistPopulationResult:
    status = await _agent_status(db, tenant_id, AGENT_KEY_ONBOARDING)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_ONBOARDING, trigger_event_type=f"hrm.{'employee_created' if kind == 'onboarding' else 'offboarding_initiated'}",
            trigger_context={"checklist_id": checklist_id, "kind": kind, "job_title": job_title, "department": department},
            reasoning=f"Agent status is {status.value} — no checklist task recommendation was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return ChecklistPopulationResult(decision_log=log, tasks_added=[], reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = hrm_tools.build_checklist_task_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=CHECKLIST_SYSTEM_PROMPT,
            user_message=f"Employee: job_title='{job_title}', department='{department}', entity='{entity_ref}'. Kind: {kind}. Recommend the checklist tasks.",
            tools=hrm_tools.CHECKLIST_TASK_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_checklist_tasks"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            tasks = submitted.get("tasks", [])
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            tasks = []
            reasoning = turn.final_text or "(agent produced no final text and never called submit_checklist_tasks)"
    else:
        tasks, reasoning = _deterministic_checklist_tasks(kind, department)

    for task_label in tasks:
        hrm_tools.add_checklist_task(tenant_id, checklist_id, task_label)
    tools_called.append({"tool": "add_checklist_task", "input": {"checklist_id": checklist_id, "count": len(tasks)}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_ONBOARDING, trigger_event_type=f"hrm.{'employee_created' if kind == 'onboarding' else 'offboarding_initiated'}",
        trigger_context={"checklist_id": checklist_id, "kind": kind, "job_title": job_title, "department": department, "entity_ref": entity_ref},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="checklist_tasks_populated" if tasks else "no_tasks_recommended",
        outcome="success" if tasks else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_ONBOARDING, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return ChecklistPopulationResult(decision_log=log, tasks_added=tasks, reasoning=reasoning)


async def handle_employee_created(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> ChecklistPopulationResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_checklist_population(
        db, tenant_id, payload.get("checklist_id", ""), "onboarding", payload.get("job_title", ""), payload.get("department", ""), payload.get("entity_ref", ""),
    )


async def handle_offboarding_initiated(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> ChecklistPopulationResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_checklist_population(
        db, tenant_id, payload.get("checklist_id", ""), "offboarding", payload.get("job_title", ""), payload.get("department", ""), payload.get("entity_ref", ""),
    )


BENEFITS_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, \
recommending a benefits plan for an employee (HRM-002).

Use get_benefits_plans to see the full catalog (type, tier, premium, employer \
contribution). Recommend the medical plan that's the best fit — absent richer signal \
about dependents or life stage, the most defensible default is the lowest \
employee-paid-cost medical plan, but use judgment if something in the role or context \
suggests otherwise. If no medical plan exists in the catalog, say so plainly rather \
than forcing a recommendation. Always finish by calling submit_benefits_recommendation."""


def _deterministic_benefits_recommendation(plans: list[dict]) -> tuple[str | None, str]:
    medical_plans = [p for p in plans if p.get("plan_type") == "medical"]
    if not medical_plans:
        return None, "[deterministic fallback — ANTHROPIC_API_KEY not configured] No medical plan exists in the catalog to recommend."

    def employee_cost(p: dict) -> float:
        return p["monthly_premium_usd"] * (1 - p.get("employer_contribution_pct", 0.0))

    best = min(medical_plans, key=employee_cost)
    return best["id"], (
        f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Lowest employee-paid-cost medical plan: "
        f"{best['name']} (${employee_cost(best):.2f}/month employee share). Richer reasoning about dependents "
        "or life stage would need a live Claude pass and more data than this fallback has."
    )


@dataclass
class BenefitsRecommendationResult:
    decision_log: DecisionLog
    recommended_plan_id: str | None
    reasoning: str
    skipped_reason: str | None = None


def _employee_benefits_cost(plan: dict) -> float:
    """What actually comes out of the employee's paycheck for this
    plan — the number the financial-risk gate compares, not the
    plan's sticker premium."""
    premium = plan.get("monthly_premium_usd", 0.0)
    employer_pct = plan.get("employer_contribution_pct", 0.0)
    return round(premium * (1 - employer_pct), 2)


def _benefits_financial_risk(plans: list[dict], enrollments: list[dict], recommended_plan_id: str) -> tuple[bool, str]:
    """The one genuine financial-risk gate in HRM: does accepting this
    recommendation raise what comes out of the employee's paycheck?
    A first-time enrollment (no active plan on file) is never a cost
    increase relative to anything, so it auto-executes; only a real,
    computed increase over an existing active enrollment holds for
    human review — this is the literal "financial risk" exception,
    not a blanket hold on every recommendation."""
    plans_by_id = {p["id"]: p for p in plans}
    recommended = plans_by_id.get(recommended_plan_id)
    if recommended is None:
        return True, "Recommended plan not found in catalog — holding for human review rather than guessing."

    active_enrollment = next((e for e in enrollments if e.get("status") == "active"), None)
    if active_enrollment is None:
        return False, "No active enrollment on file — first-time enrollment, not a cost increase."

    current_plan = plans_by_id.get(active_enrollment.get("plan_id"))
    if current_plan is None:
        return True, "Could not resolve the employee's current plan for comparison — holding for human review rather than assuming no increase."

    current_cost = _employee_benefits_cost(current_plan)
    recommended_cost = _employee_benefits_cost(recommended)
    if recommended_cost > current_cost:
        return True, f"Recommended plan raises the employee's monthly cost from ${current_cost} to ${recommended_cost} — financial risk, held for human review."
    return False, f"Recommended plan's employee cost (${recommended_cost}) does not exceed the current plan's (${current_cost}) — no financial risk."


async def evaluate_benefits_recommendation(
    db: AsyncSession,
    tenant_id: str,
    employee_id: str,
    job_title: str,
    department: str,
    client: AnthropicAgentClient | None = None,
) -> BenefitsRecommendationResult:
    """HRM-002 — runs automatically the instant hrm-svc publishes
    hrm.benefits_recommendation_requested. Fully automated per the
    engineering directive: the recommendation is auto-accepted and
    the employee auto-enrolled UNLESS doing so would raise their
    monthly out-of-pocket cost versus their current active enrollment
    — that specific case is the financial-risk exception, held for
    human review rather than executed blind."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_BENEFITS)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_BENEFITS, trigger_event_type="hrm.benefits_recommendation_requested",
            trigger_context={"employee_id": employee_id, "job_title": job_title, "department": department},
            reasoning=f"Agent status is {status.value} — no benefits recommendation was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return BenefitsRecommendationResult(decision_log=log, recommended_plan_id=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = hrm_tools.build_benefits_recommendation_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=BENEFITS_SYSTEM_PROMPT,
            user_message=f"Employee: job_title='{job_title}', department='{department}'. Recommend a benefits plan.",
            tools=hrm_tools.BENEFITS_RECOMMENDATION_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_benefits_recommendation"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            plan_id = submitted.get("recommended_plan_id")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            plan_id = None
            reasoning = turn.final_text or "(agent produced no final text and never called submit_benefits_recommendation)"
    else:
        plans_response = hrm_tools.get_benefits_plans(tenant_id)
        plans = plans_response.get("plans", [])
        tools_called = [{"tool": "get_benefits_plans", "input": {}}]
        plan_id, reasoning = _deterministic_benefits_recommendation(plans)

    action_taken = "no_recommendation_made"
    if plan_id:
        created = hrm_tools.create_benefits_recommendation(tenant_id, employee_id, plan_id, reasoning)
        tools_called.append({"tool": "create_benefits_recommendation", "input": {"employee_id": employee_id, "recommended_plan_id": plan_id}})
        action_taken = "benefits_recommendation_created_pending_financial_review"

        plans_response = hrm_tools.get_benefits_plans(tenant_id)
        plans = plans_response.get("plans", [])
        enrollments = hrm_tools.get_employee_benefits_enrollments(tenant_id, employee_id)
        tools_called.append({"tool": "get_employee_benefits_enrollments", "input": {"employee_id": employee_id}})
        is_risk, risk_note = _benefits_financial_risk(plans, enrollments, plan_id)
        reasoning = reasoning + " | " + risk_note

        recommendation_id = created.get("id")
        if not is_risk and recommendation_id:
            gated = await _hrm_gate(
                db, tenant_id, AGENT_KEY_BENEFITS, "benefits.recommendation_apply", "hrm.accept_benefits_recommendation",
                {"recommendation_id": recommendation_id}, reasoning, "hrm.benefits_recommendation_requested",
            )
            if gated.executed:
                tools_called.append({"tool": "accept_benefits_recommendation", "input": {"recommendation_id": recommendation_id}})
                action_taken = "benefits_recommendation_auto_accepted"
            else:
                action_taken = "benefits_recommendation_queued_for_approval"

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_BENEFITS, trigger_event_type="hrm.benefits_recommendation_requested",
        trigger_context={"employee_id": employee_id, "job_title": job_title, "department": department},
        reasoning=reasoning, tools_called=tools_called,
        action_taken=action_taken,
        outcome="success" if plan_id else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_BENEFITS, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return BenefitsRecommendationResult(decision_log=log, recommended_plan_id=plan_id, reasoning=reasoning)


async def handle_benefits_recommendation_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> BenefitsRecommendationResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_benefits_recommendation(
        db, tenant_id, payload.get("employee_id", ""), payload.get("job_title", ""), payload.get("department", ""),
    )


SCREENING_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, \
screening a resume against a job requisition's requirements (HRM-003).

Read the candidate's resume summary and the requisition's required skills. Score the \
fit 0.0-1.0 based only on skills, experience, and qualifications — never on the \
candidate's name, or anything that could proxy for a protected characteristic. A false \
high score wastes a recruiter's time on a poor fit; a false low score could screen out \
a genuinely qualified candidate, so weigh both risks. This score is a signal for a \
recruiter, not a decision — nothing here rejects anyone automatically. Always finish by \
calling submit_screening_score."""

NURTURE_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, drafting \
a candidate nurture message after a rejection (HRM-003) — the "silver medalist" \
practice of keeping strong candidates warm for future roles rather than letting the \
relationship go cold.

Write a genuinely warm, specific message — but you are never sending this yourself; a \
recruiter always reviews before it goes out, so don't hedge the draft into uselessness. \
Always finish by calling submit_nurture_draft."""

_DETERMINISTIC_NURTURE_TEMPLATE = (
    "Thank you for your interest in joining VOYQI, {name}. While we've moved forward with another candidate for "
    "this particular role, we were impressed with your background and would like to keep your profile on file "
    "for future opportunities that may be a better fit."
)


def _deterministic_screening_score(resume_summary: str, required_skills: str) -> tuple[float, str]:
    required = [s.strip().lower() for s in required_skills.split(",") if s.strip()]
    if not required:
        return 0.5, "[deterministic fallback — ANTHROPIC_API_KEY not configured] No required skills listed on the requisition — defaulting to a neutral score pending fuller job requirements."

    resume_lower = resume_summary.lower()
    matched = [r for r in required if r in resume_lower]
    score = round(len(matched) / len(required), 2)
    reasoning = (
        f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Keyword overlap: {len(matched)}/{len(required)} "
        f"required skills found in the resume summary ({', '.join(matched) if matched else 'none'}). Skills/qualifications "
        "only — never name or demographic proxies."
    )
    return score, reasoning


@dataclass
class ScreeningResult:
    decision_log: DecisionLog
    score: float | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_application_screening(
    db: AsyncSession,
    tenant_id: str,
    application_id: str,
    resume_summary: str,
    required_skills: str,
    client: AnthropicAgentClient | None = None,
) -> ScreeningResult:
    """HRM-003 — runs automatically the instant hrm-svc publishes
    hrm.application_submitted. Scores fit; a human recruiter still
    decides every stage past SCREENING."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_RECRUITING)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_RECRUITING, trigger_event_type="hrm.application_submitted",
            trigger_context={"application_id": application_id},
            reasoning=f"Agent status is {status.value} — no screening was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return ScreeningResult(decision_log=log, score=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = hrm_tools.build_screening_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=SCREENING_SYSTEM_PROMPT,
            user_message=f"Resume summary: '{resume_summary}'\nRequired skills: '{required_skills}'. Score the fit.",
            tools=hrm_tools.SCREENING_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_screening_score"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            score = submitted.get("score", 0.5)
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            score = 0.5
            reasoning = turn.final_text or "(agent produced no final text and never called submit_screening_score)"
    else:
        score, reasoning = _deterministic_screening_score(resume_summary, required_skills)

    hrm_tools.apply_screening_score(tenant_id, application_id, score, reasoning)
    tools_called.append({"tool": "apply_screening_score", "input": {"application_id": application_id, "score": score}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_RECRUITING, trigger_event_type="hrm.application_submitted",
        trigger_context={"application_id": application_id, "required_skills": required_skills},
        reasoning=reasoning, tools_called=tools_called, action_taken="screening_score_applied", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_RECRUITING, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return ScreeningResult(decision_log=log, score=score, reasoning=reasoning)


async def handle_application_submitted(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> ScreeningResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_application_screening(
        db, tenant_id, payload.get("application_id", ""), payload.get("resume_summary", ""), payload.get("required_skills", ""),
    )


@dataclass
class NurtureDraftResult:
    decision_log: DecisionLog
    draft_created: bool
    reasoning: str
    skipped_reason: str | None = None
    draft_sent: bool = False


async def evaluate_candidate_nurture(
    db: AsyncSession,
    tenant_id: str,
    candidate_id: str,
    application_id: str,
    candidate_name: str,
    reason: str,
    client: AnthropicAgentClient | None = None,
) -> NurtureDraftResult:
    """HRM-003 — runs automatically the instant hrm-svc publishes
    hrm.application_rejected. Full automation: creates the draft and
    immediately sends it — no financial or security risk in a silver-
    medalist nurture message, so the financial/security-risk carve-out
    does not apply. The draft row still exists for audit purposes;
    approve_and_send_nurture is simply called by this agent instead of
    a human."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_RECRUITING)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_RECRUITING, trigger_event_type="hrm.application_rejected",
            trigger_context={"candidate_id": candidate_id, "application_id": application_id},
            reasoning=f"Agent status is {status.value} — no nurture draft was created.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return NurtureDraftResult(decision_log=log, draft_created=False, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = hrm_tools.build_nurture_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=NURTURE_SYSTEM_PROMPT,
            user_message=f"Candidate '{candidate_name}' was not selected. Reason on file: '{reason or '(none given)'}'. Draft a nurture message.",
            tools=hrm_tools.NURTURE_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_nurture_draft"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            draft_body = submitted.get("draft_body", "")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            draft_body = ""
            reasoning = turn.final_text or "(agent produced no final text and never called submit_nurture_draft)"
    else:
        draft_body = _DETERMINISTIC_NURTURE_TEMPLATE.format(name=candidate_name or "there")
        reasoning = "[deterministic fallback — ANTHROPIC_API_KEY not configured] Fixed, honest template — never creative, structurally incapable of saying something inappropriate."

    draft_created = False
    draft_sent = False
    if draft_body:
        created = hrm_tools.create_nurture_draft(tenant_id, candidate_id, draft_body, reasoning, application_id)
        tools_called.append({"tool": "create_nurture_draft", "input": {"candidate_id": candidate_id}})
        draft_created = True
        if created.get("id"):
            gated = await _hrm_gate(
                db, tenant_id, AGENT_KEY_RECRUITING, "candidate.nurture_send", "hrm.send_nurture_draft",
                {"draft_id": created["id"]}, reasoning, "hrm.application_rejected",
            )
            if gated.executed:
                tools_called.append({"tool": "send_nurture_draft", "input": {"draft_id": created["id"]}})
                draft_sent = bool(gated.result)

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_RECRUITING, trigger_event_type="hrm.application_rejected",
        trigger_context={"candidate_id": candidate_id, "application_id": application_id, "candidate_name": candidate_name, "reason": reason},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="nurture_draft_created_and_sent" if draft_sent else ("nurture_draft_created" if draft_created else "no_draft_created"),
        outcome="success" if draft_created else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_RECRUITING, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return NurtureDraftResult(decision_log=log, draft_created=draft_created, reasoning=reasoning, draft_sent=draft_sent)


async def handle_application_rejected(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> NurtureDraftResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_candidate_nurture(
        db, tenant_id, payload.get("candidate_id", ""), payload.get("application_id", ""), payload.get("candidate_name", ""), payload.get("reason", ""),
    )


FEEDBACK_DRAFT_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, \
turning a manager's raw review notes into polished narrative feedback (HRM-004).

Write feedback that's genuinely useful and specific — but you are never finalizing this \
yourself; the manager always reviews and can edit before it's saved to the employee's \
record. The manager's rating and raw notes are their real judgment; you're only \
improving the writing, never changing the substance of what they observed. Always \
finish by calling submit_feedback_draft."""

NINE_BOX_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, \
suggesting a 9-box grid placement (performance x potential) for succession planning \
and calibration (HRM-004).

You have the employee's numeric rating (performance signal) and goal completion rate \
(one proxy for potential — not the only one, so use judgment about what else might be \
relevant). This placement is only ever a suggestion for a calibration session — a \
human always confirms it, no matter how confident you are, because a 9-box placement \
drives real promotion and succession decisions. Always finish by calling \
submit_nine_box_placement."""

_RATING_DESCRIPTORS = [(4.5, "an outstanding"), (3.5, "a strong"), (2.5, "a solid"), (1.5, "a developing"), (0.0, "a challenging")]


def _rating_descriptor(rating: float | None) -> str:
    if rating is None:
        return "a"
    for threshold, descriptor in _RATING_DESCRIPTORS:
        if rating >= threshold:
            return descriptor
    return "a challenging"


def _deterministic_feedback_draft(manager_notes: str, rating: float | None) -> tuple[str, str]:
    descriptor = _rating_descriptor(rating)
    if manager_notes.strip():
        body = f"This employee had {descriptor} review period. Manager notes: {manager_notes.strip()}"
    else:
        body = f"This employee had {descriptor} review period. No detailed manager notes were provided — please add specifics before finalizing."
    reasoning = "[deterministic fallback — ANTHROPIC_API_KEY not configured] Template narrative built directly from the manager's raw notes and numeric rating, not a creative rewrite."
    return body, reasoning


def _level_from_score(score: float | None, low_threshold: float, high_threshold: float, default: str = "medium") -> str:
    if score is None:
        return default
    if score >= high_threshold:
        return "high"
    if score >= low_threshold:
        return "medium"
    return "low"


def _deterministic_nine_box(rating: float | None, goal_completion_rate: float | None) -> tuple[str, str, str]:
    performance_axis = _level_from_score(rating, 2.5, 4.0)
    potential_axis = _level_from_score(goal_completion_rate, 0.4, 0.75)
    reasoning = (
        f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Performance axis from rating "
        f"({rating if rating is not None else 'not provided'}), potential axis from goal completion rate "
        f"({f'{goal_completion_rate:.0%}' if goal_completion_rate is not None else 'not provided'})."
    )
    return performance_axis, potential_axis, reasoning


@dataclass
class FeedbackDraftResult:
    decision_log: DecisionLog
    draft_created: bool
    reasoning: str
    skipped_reason: str | None = None
    draft_finalized: bool = False


async def evaluate_feedback_draft(
    db: AsyncSession,
    tenant_id: str,
    review_id: str,
    manager_notes: str,
    rating: float | None,
    client: AnthropicAgentClient | None = None,
) -> FeedbackDraftResult:
    """HRM-004 — runs automatically once a manager requests it via
    request_feedback_draft. Full automation: creates the draft and
    immediately finalizes it — this only polishes the manager's own
    notes into narrative form and never touches the underlying
    rating, so no financial or security risk applies."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_PERFORMANCE)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_PERFORMANCE, trigger_event_type="hrm.feedback_draft_requested",
            trigger_context={"review_id": review_id},
            reasoning=f"Agent status is {status.value} — no feedback draft was created.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return FeedbackDraftResult(decision_log=log, draft_created=False, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = hrm_tools.build_feedback_draft_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=FEEDBACK_DRAFT_SYSTEM_PROMPT,
            user_message=f"Manager notes: '{manager_notes}'\nRating: {rating}. Draft polished narrative feedback.",
            tools=hrm_tools.FEEDBACK_DRAFT_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_feedback_draft"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            draft_body = submitted.get("draft_body", "")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            draft_body = ""
            reasoning = turn.final_text or "(agent produced no final text and never called submit_feedback_draft)"
    else:
        draft_body, reasoning = _deterministic_feedback_draft(manager_notes, rating)

    draft_created = False
    draft_finalized = False
    if draft_body:
        created = hrm_tools.create_feedback_draft(tenant_id, review_id, draft_body, reasoning)
        tools_called.append({"tool": "create_feedback_draft", "input": {"review_id": review_id}})
        draft_created = True
        if created.get("id"):
            gated = await _hrm_gate(
                db, tenant_id, AGENT_KEY_PERFORMANCE, "feedback.finalize", "hrm.finalize_feedback_draft",
                {"draft_id": created["id"]}, reasoning, "hrm.feedback_draft_requested",
            )
            if gated.executed:
                tools_called.append({"tool": "finalize_feedback_draft", "input": {"draft_id": created["id"]}})
                draft_finalized = bool(gated.result)

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_PERFORMANCE, trigger_event_type="hrm.feedback_draft_requested",
        trigger_context={"review_id": review_id, "manager_notes": manager_notes, "rating": rating},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="feedback_draft_created_and_finalized" if draft_finalized else ("feedback_draft_created" if draft_created else "no_draft_created"),
        outcome="success" if draft_created else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_PERFORMANCE, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return FeedbackDraftResult(decision_log=log, draft_created=draft_created, reasoning=reasoning, draft_finalized=draft_finalized)


async def handle_feedback_draft_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> FeedbackDraftResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_feedback_draft(db, tenant_id, payload.get("review_id", ""), payload.get("manager_notes", ""), payload.get("rating"))


@dataclass
class NineBoxResult:
    decision_log: DecisionLog
    placement_created: bool
    performance_axis: str | None
    potential_axis: str | None
    reasoning: str
    skipped_reason: str | None = None
    placement_confirmed: bool = False


async def evaluate_nine_box_placement(
    db: AsyncSession,
    tenant_id: str,
    employee_id: str,
    review_cycle_id: str,
    rating: float | None,
    goal_completion_rate: float | None,
    client: AnthropicAgentClient | None = None,
) -> NineBoxResult:
    """HRM-004 — runs automatically the instant a review cycle closes.
    Full automation: creates the placement and immediately confirms
    it — a talent-calibration categorization, not itself a financial
    transaction, so no financial-risk carve-out applies."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_PERFORMANCE)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_PERFORMANCE, trigger_event_type="hrm.nine_box_evaluation_requested",
            trigger_context={"employee_id": employee_id, "review_cycle_id": review_cycle_id},
            reasoning=f"Agent status is {status.value} — no 9-box placement was suggested.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return NineBoxResult(decision_log=log, placement_created=False, performance_axis=None, potential_axis=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = hrm_tools.build_nine_box_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=NINE_BOX_SYSTEM_PROMPT,
            user_message=f"Rating: {rating}. Goal completion rate: {goal_completion_rate}. Suggest a 9-box placement.",
            tools=hrm_tools.NINE_BOX_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_nine_box_placement"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            performance_axis = submitted.get("performance_axis")
            potential_axis = submitted.get("potential_axis")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            performance_axis, potential_axis = None, None
            reasoning = turn.final_text or "(agent produced no final text and never called submit_nine_box_placement)"
    else:
        performance_axis, potential_axis, reasoning = _deterministic_nine_box(rating, goal_completion_rate)

    placement_created = False
    placement_confirmed = False
    if performance_axis and potential_axis:
        created = hrm_tools.create_nine_box_placement(tenant_id, employee_id, review_cycle_id, performance_axis, potential_axis, reasoning)
        tools_called.append({"tool": "create_nine_box_placement", "input": {"employee_id": employee_id, "review_cycle_id": review_cycle_id}})
        placement_created = True
        if created.get("id"):
            gated = await _hrm_gate(
                db, tenant_id, AGENT_KEY_PERFORMANCE, "nine_box.placement_confirm", "hrm.confirm_nine_box_placement",
                {"placement_id": created["id"]}, reasoning, "hrm.nine_box_evaluation_requested",
            )
            if gated.executed:
                tools_called.append({"tool": "confirm_nine_box_placement", "input": {"placement_id": created["id"]}})
                placement_confirmed = bool(gated.result)

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_PERFORMANCE, trigger_event_type="hrm.nine_box_evaluation_requested",
        trigger_context={"employee_id": employee_id, "review_cycle_id": review_cycle_id, "rating": rating, "goal_completion_rate": goal_completion_rate},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="nine_box_placement_created_and_confirmed" if placement_confirmed else ("nine_box_placement_created" if placement_created else "no_placement_created"),
        outcome="success" if placement_created else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_PERFORMANCE, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return NineBoxResult(decision_log=log, placement_created=placement_created, performance_axis=performance_axis, potential_axis=potential_axis, reasoning=reasoning, placement_confirmed=placement_confirmed)


async def handle_nine_box_evaluation_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> NineBoxResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_nine_box_placement(
        db, tenant_id, payload.get("employee_id", ""), payload.get("review_cycle_id", ""), payload.get("rating"), payload.get("goal_completion_rate"),
    )


ATTRITION_RISK_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, \
assessing attrition risk (HRM-005) — the same discipline as the Member Experience \
Agent's churn scoring.

You have real, auditable factors: tenure, where compensation sits in the role's salary \
band, months since the last compensation change, and goal completion rate. Score risk \
0.0-1.0. This is a signal for HR to act on — a conversation, a retention offer, a comp \
review — never an automatic decision about anyone's employment. Weigh the factors \
honestly rather than either downplaying real risk or overreacting to a single weak \
signal. Always finish by calling submit_attrition_risk."""

_ATTRITION_FACTOR_WEIGHTS = {
    "low_comp_band_position": 0.35,
    "comp_stagnation": 0.25,
    "low_goal_completion": 0.20,
    "short_tenure": 0.15,
}


def _deterministic_attrition_risk(
    tenure_months: int | None,
    comp_band_position: float | None,
    months_since_last_comp_change: int | None,
    goal_completion_rate: float | None,
) -> tuple[float, str]:
    """A real, auditable RFM-style heuristic — the same principle as
    the Member Experience Agent's churn scoring. Each driver only fires
    on a real signal; absent data contributes nothing rather than
    being treated as risk."""
    score = 0.0
    drivers = []

    if comp_band_position is not None and comp_band_position < 0.3:
        score += _ATTRITION_FACTOR_WEIGHTS["low_comp_band_position"]
        drivers.append(f"compensation sits in the bottom 30% of their pay band ({comp_band_position:.0%})")

    if months_since_last_comp_change is not None and months_since_last_comp_change > 18:
        score += _ATTRITION_FACTOR_WEIGHTS["comp_stagnation"]
        drivers.append(f"{months_since_last_comp_change} months since last compensation change — pay stagnation")

    if goal_completion_rate is not None and goal_completion_rate < 0.3:
        score += _ATTRITION_FACTOR_WEIGHTS["low_goal_completion"]
        drivers.append(f"low goal completion rate ({goal_completion_rate:.0%}) — possible disengagement")

    if tenure_months is not None and tenure_months < 6:
        score += _ATTRITION_FACTOR_WEIGHTS["short_tenure"]
        drivers.append(f"short tenure ({tenure_months} months) — early-tenure attrition risk window")

    score = round(min(score, 1.0), 2)
    if not drivers:
        drivers.append("no elevated risk signals detected")
    reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {'; '.join(drivers)}."
    return score, reasoning


@dataclass
class AttritionRiskResult:
    decision_log: DecisionLog
    risk_score: float | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_attrition_risk(
    db: AsyncSession,
    tenant_id: str,
    employee_id: str,
    tenure_months: int | None = None,
    comp_band_position: float | None = None,
    months_since_last_comp_change: int | None = None,
    goal_completion_rate: float | None = None,
    client: AnthropicAgentClient | None = None,
) -> AttritionRiskResult:
    """HRM-005 — runs automatically once HR requests an assessment.
    record_attrition_risk in hrm-svc is the only function that ever
    writes the score; this never applies an automatic action against
    anyone's employment."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_WORKFORCE_ANALYTICS)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_WORKFORCE_ANALYTICS, trigger_event_type="hrm.attrition_risk_requested",
            trigger_context={"employee_id": employee_id},
            reasoning=f"Agent status is {status.value} — no attrition risk was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return AttritionRiskResult(decision_log=log, risk_score=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []
    factors = {
        "tenure_months": tenure_months, "comp_band_position": comp_band_position,
        "months_since_last_comp_change": months_since_last_comp_change, "goal_completion_rate": goal_completion_rate,
    }

    if client.is_configured:
        dispatch = hrm_tools.build_attrition_risk_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=ATTRITION_RISK_SYSTEM_PROMPT,
            user_message=f"Factors: {factors}. Assess attrition risk.",
            tools=hrm_tools.ATTRITION_RISK_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_attrition_risk"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            risk_score = submitted.get("risk_score", 0.5)
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            risk_score = 0.5
            reasoning = turn.final_text or "(agent produced no final text and never called submit_attrition_risk)"
    else:
        risk_score, reasoning = _deterministic_attrition_risk(tenure_months, comp_band_position, months_since_last_comp_change, goal_completion_rate)

    hrm_tools.record_attrition_risk(tenant_id, employee_id, risk_score, reasoning)
    tools_called.append({"tool": "record_attrition_risk", "input": {"employee_id": employee_id, "risk_score": risk_score}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_WORKFORCE_ANALYTICS, trigger_event_type="hrm.attrition_risk_requested",
        trigger_context={"employee_id": employee_id, **factors},
        reasoning=reasoning, tools_called=tools_called, action_taken="attrition_risk_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_WORKFORCE_ANALYTICS, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return AttritionRiskResult(decision_log=log, risk_score=risk_score, reasoning=reasoning)


async def handle_attrition_risk_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> AttritionRiskResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_attrition_risk(
        db, tenant_id, payload.get("employee_id", ""), payload.get("tenure_months"), payload.get("comp_band_position"),
        payload.get("months_since_last_comp_change"), payload.get("goal_completion_rate"),
    )


COURSE_RECOMMENDATION_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native \
HRM, recommending the next VOYQI Academy course for an employee (HRM-006).

You're given a list of mandatory courses this employee hasn't completed yet. Recommend \
the single highest-priority one — compliance and safety courses always outrank optional \
development courses, since they carry real regulatory and safety consequences. This is \
only ever a suggestion; accepting it and actually enrolling is always a human (employee \
or manager) action. Always finish by calling submit_course_recommendation."""

_CATEGORY_PRIORITY_HINTS = ["compliance", "safety", "harassment", "security", "safety protocol"]


def _deterministic_course_recommendation(incomplete_course_ids: list[str], incomplete_course_titles: list[str]) -> tuple[str | None, str]:
    """Real, deterministic priority ordering: any title matching a
    compliance/safety keyword is recommended first; otherwise the
    first incomplete mandatory course on file. No AI needed for this
    — it's a fixed priority rule, not a judgment call."""
    if not incomplete_course_ids:
        return None, "[deterministic fallback — ANTHROPIC_API_KEY not configured] No incomplete mandatory courses on file — nothing to recommend."

    for course_id, title in zip(incomplete_course_ids, incomplete_course_titles, strict=False):
        if any(hint in title.lower() for hint in _CATEGORY_PRIORITY_HINTS):
            return course_id, f"[deterministic fallback — ANTHROPIC_API_KEY not configured] '{title}' is a compliance/safety course and always outranks other incomplete mandatory training."

    return incomplete_course_ids[0], f"[deterministic fallback — ANTHROPIC_API_KEY not configured] '{incomplete_course_titles[0]}' is the next incomplete mandatory course on file."


@dataclass
class CourseRecommendationResult:
    decision_log: DecisionLog
    recommendation_created: bool
    recommended_course_id: str | None
    reasoning: str
    skipped_reason: str | None = None
    enrollment_completed: bool = False


async def evaluate_course_recommendation(
    db: AsyncSession,
    tenant_id: str,
    employee_id: str,
    incomplete_mandatory_course_ids: list[str],
    incomplete_mandatory_course_titles: list[str],
    client: AnthropicAgentClient | None = None,
) -> CourseRecommendationResult:
    """HRM-006 — runs automatically once requested. Full automation:
    creates the recommendation and immediately enrolls the employee
    when a course was actually recommended. VOYQI Academy courses
    carry no external cost field in this build, so no financial-risk
    carve-out applies to enrollment itself."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_ACADEMY)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_ACADEMY, trigger_event_type="hrm.course_recommendation_requested",
            trigger_context={"employee_id": employee_id},
            reasoning=f"Agent status is {status.value} — no course recommendation was created.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return CourseRecommendationResult(decision_log=log, recommendation_created=False, recommended_course_id=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = hrm_tools.build_course_recommendation_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=COURSE_RECOMMENDATION_SYSTEM_PROMPT,
            user_message=f"Incomplete mandatory courses: {list(zip(incomplete_mandatory_course_ids, incomplete_mandatory_course_titles, strict=False))}. Recommend the highest-priority one.",
            tools=hrm_tools.COURSE_RECOMMENDATION_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_course_recommendation"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            recommended_course_id = submitted.get("recommended_course_id")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            recommended_course_id = None
            reasoning = turn.final_text or "(agent produced no final text and never called submit_course_recommendation)"
    else:
        recommended_course_id, reasoning = _deterministic_course_recommendation(incomplete_mandatory_course_ids, incomplete_mandatory_course_titles)

    created = hrm_tools.create_course_recommendation(tenant_id, employee_id, recommended_course_id, reasoning)
    tools_called.append({"tool": "create_course_recommendation", "input": {"employee_id": employee_id, "recommended_course_id": recommended_course_id}})

    enrollment_completed = False
    if recommended_course_id and created.get("id"):
        gated = await _hrm_gate(
            db, tenant_id, AGENT_KEY_ACADEMY, "course.recommendation_assign", "hrm.accept_course_recommendation",
            {"recommendation_id": created["id"]}, reasoning, "hrm.course_recommendation_requested",
        )
        if gated.executed:
            tools_called.append({"tool": "accept_course_recommendation", "input": {"recommendation_id": created["id"]}})
            enrollment_completed = bool(gated.result)

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_ACADEMY, trigger_event_type="hrm.course_recommendation_requested",
        trigger_context={"employee_id": employee_id, "incomplete_mandatory_course_ids": incomplete_mandatory_course_ids},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="course_recommendation_created_and_enrolled" if enrollment_completed else "course_recommendation_created", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_ACADEMY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return CourseRecommendationResult(decision_log=log, recommendation_created=True, recommended_course_id=recommended_course_id, reasoning=reasoning, enrollment_completed=enrollment_completed)


async def handle_course_recommendation_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> CourseRecommendationResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_course_recommendation(
        db, tenant_id, payload.get("employee_id", ""), payload.get("incomplete_mandatory_course_ids", []), payload.get("incomplete_mandatory_course_titles", []),
    )


POLICY_QA_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, \
answering an employee's policy question by drafting a grounded reply (HRM-007).

You're given the actual policy documents on file. Answer ONLY using what's actually in \
them — if none of the provided policies actually address the question, say so honestly \
in your reasoning rather than inventing an answer; a wrong policy answer is worse than \
no answer. Cite which policy your answer draws from. You are never sending this reply \
yourself — HR always reviews before it goes to the employee. Always finish by calling \
submit_policy_answer."""

_STOPWORDS = {"the", "a", "an", "is", "are", "can", "i", "do", "does", "my", "to", "of", "for", "in", "on", "and", "or", "what", "how", "when", "policy", "policies", "our"}


def _deterministic_policy_answer(question: str, policy_documents: list[dict]) -> tuple[str, str, str | None]:
    """A real, auditable keyword-overlap grounding heuristic — the same
    principle as HRM-003's resume screening. Picks the policy document
    with the most question-keyword overlap; if nothing overlaps at
    all, says so honestly instead of drafting a guess."""
    question_words = {w.strip(".,?!") for w in question.lower().split() if w.strip(".,?!") not in _STOPWORDS and len(w) > 2}
    if not question_words or not policy_documents:
        return "", "[deterministic fallback — ANTHROPIC_API_KEY not configured] No policy documents on file to ground an answer in — this needs a human response.", None

    best_doc = None
    best_overlap = 0
    for doc in policy_documents:
        doc_words = {w.strip(".,?!") for w in (doc.get("title", "") + " " + doc.get("body", "")).lower().split()}
        overlap = len(question_words & doc_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_doc = doc

    if best_doc is None or best_overlap == 0:
        return "", "[deterministic fallback — ANTHROPIC_API_KEY not configured] None of the policies on file match this question closely enough to ground an answer — this needs a human response.", None

    draft_body = f"Per our {best_doc['title']}: {best_doc['body']}"
    reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Keyword overlap with '{best_doc['title']}' ({best_overlap} matching terms) — grounding the answer directly in that policy's text."
    return draft_body, reasoning, best_doc.get("id")


@dataclass
class PolicyAnswerResult:
    decision_log: DecisionLog
    draft_created: bool
    reasoning: str
    skipped_reason: str | None = None
    reply_sent: bool = False


async def evaluate_policy_question(
    db: AsyncSession,
    tenant_id: str,
    case_id: str,
    question: str,
    client: AnthropicAgentClient | None = None,
) -> PolicyAnswerResult:
    """HRM-007 — runs automatically on every inbound policy-question
    message. Fetches the real policy catalog itself (the same pattern
    as evaluate_benefits_recommendation's get_benefits_plans call)
    rather than depending on the triggering event to carry it.
    Full automation: creates the draft and immediately sends it
    whenever it's genuinely grounded in a real policy document — no
    financial or security risk in answering a policy question. If
    nothing grounds an answer, no draft is created at all — an
    ungrounded guess is worse than silence, and that stays true
    regardless of automation level; it's an honesty behavior, not a
    human-approval gate."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_CASE_MANAGEMENT)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_CASE_MANAGEMENT, trigger_event_type="hrm.policy_question_asked",
            trigger_context={"case_id": case_id, "question": question},
            reasoning=f"Agent status is {status.value} — no policy answer was drafted.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return PolicyAnswerResult(decision_log=log, draft_created=False, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []
    cites_policy_id = None

    policy_documents = hrm_tools.get_policy_documents(tenant_id)
    tools_called.append({"tool": "get_policy_documents", "input": {}})

    if client.is_configured:
        dispatch = hrm_tools.build_policy_answer_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=POLICY_QA_SYSTEM_PROMPT,
            user_message=f"Question: '{question}'\nPolicy documents on file: {policy_documents}. Draft a grounded answer, or say if none apply.",
            tools=hrm_tools.POLICY_ANSWER_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called += [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_policy_answer"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            draft_body = submitted.get("draft_body", "")
            reasoning = submitted.get("reasoning", turn.final_text or "")
            cites_policy_id = submitted.get("cites_policy_id")
        else:
            draft_body = ""
            reasoning = turn.final_text or "(agent produced no final text and never called submit_policy_answer)"
    else:
        draft_body, reasoning, cites_policy_id = _deterministic_policy_answer(question, policy_documents)

    draft_created = False
    reply_sent = False
    if draft_body:
        created = hrm_tools.create_case_draft_reply(tenant_id, case_id, draft_body, reasoning, cites_policy_id)
        tools_called.append({"tool": "create_case_draft_reply", "input": {"case_id": case_id, "cites_policy_id": cites_policy_id}})
        draft_created = True
        if created.get("id"):
            gated = await _hrm_gate(
                db, tenant_id, AGENT_KEY_CASE_MANAGEMENT, "case.reply_send", "hrm.send_case_reply",
                {"draft_id": created["id"]}, reasoning, "hrm.policy_question_asked",
            )
            if gated.executed:
                tools_called.append({"tool": "send_case_reply", "input": {"draft_id": created["id"]}})
                reply_sent = bool(gated.result)

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_CASE_MANAGEMENT, trigger_event_type="hrm.policy_question_asked",
        trigger_context={"case_id": case_id, "question": question},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="case_draft_reply_created_and_sent" if reply_sent else ("case_draft_reply_created" if draft_created else "no_draft_created_ungrounded"),
        outcome="success" if draft_created else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_CASE_MANAGEMENT, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return PolicyAnswerResult(decision_log=log, draft_created=draft_created, reasoning=reasoning, reply_sent=reply_sent)


async def handle_policy_question_asked(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> PolicyAnswerResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_policy_question(db, tenant_id, payload.get("case_id", ""), payload.get("question", ""))


NOTE_UPDATE_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, \
logging documentation on an employee's record (HRM-008).

This is internal record-keeping, not a decision — you're documenting that something \
happened (a case resolved, training completed, a review finalized), not judging the \
employee or deciding anything about their employment. Write a short, factual entry. \
You must decide whether to add a brand-new note or append to the most recent existing \
one — if there's a recent note that's clearly still the active thread of context, \
append; otherwise start a new one. You can NEVER overwrite or remove what's already \
written, only add to it. Always finish by calling submit_note_entry."""


def _deterministic_note_entry(context: str) -> str:
    """A real, defensible template — factual and short, matching the
    system prompt's own instruction. No creative embellishment; this
    is documentation, not a message to anyone."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"[{timestamp}] {context.strip()}"


@dataclass
class NoteUpdateResult:
    decision_log: DecisionLog
    note_written: bool
    action_taken: str
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_note_update(
    db: AsyncSession,
    tenant_id: str,
    employee_id: str,
    context: str,
    client: AnthropicAgentClient | None = None,
) -> NoteUpdateResult:
    """HRM-008 — the one AI-first capability on this platform that
    writes directly without requiring human approval first: notes are
    additive documentation, not a decision affecting anyone's pay,
    benefits, or employment. The one rule that still can't be broken —
    enforced by append_to_note in hrm-svc, not by this agent's good
    behavior — is that an update can only ever add text, never replace
    or delete a human's original words."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_COMMUNICATION)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_COMMUNICATION, trigger_event_type="hrm.note_update_requested",
            trigger_context={"employee_id": employee_id, "context": context},
            reasoning=f"Agent status is {status.value} — no note was written.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return NoteUpdateResult(decision_log=log, note_written=False, action_taken="skipped_agent_not_active", reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    most_recent = hrm_tools.get_most_recent_note(tenant_id, employee_id)
    tools_called.append({"tool": "get_most_recent_note", "input": {"employee_id": employee_id}})

    if client.is_configured:
        dispatch = hrm_tools.build_note_entry_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=NOTE_UPDATE_SYSTEM_PROMPT,
            user_message=f"Context to log: '{context}'. Most recent existing note (if any): {most_recent}. Decide add-new vs append, and write the entry.",
            tools=hrm_tools.NOTE_ENTRY_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called += [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_note_entry"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            entry_text = submitted.get("entry_text", "")
            should_append = submitted.get("append_to_existing", False)
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            entry_text = ""
            should_append = False
            reasoning = turn.final_text or "(agent produced no final text and never called submit_note_entry)"
    else:
        entry_text = _deterministic_note_entry(context)
        # deterministic rule: append if a note exists at all, otherwise start one
        should_append = most_recent is not None
        reasoning = "[deterministic fallback — ANTHROPIC_API_KEY not configured] " + ("Appending to the most recent existing note." if should_append else "No existing note on file — starting a new one.")

    action_taken = "no_note_written"
    note_written = False
    if entry_text:
        if should_append and most_recent:
            executor, args, tool, verb = "hrm.append_employee_note", {"note_id": most_recent["id"], "additional_text": entry_text}, "append_employee_note", "note_appended"
        else:
            executor, args, tool, verb = "hrm.add_employee_note", {"employee_id": employee_id, "body": entry_text}, "add_employee_note", "note_added"
        gated = await _hrm_gate(db, tenant_id, AGENT_KEY_COMMUNICATION, "employee_note.write", executor, args, reasoning, "hrm.note_update_requested")
        if gated.executed:
            tools_called.append({"tool": tool, "input": {k: v for k, v in args.items() if k not in ("additional_text", "body")}})
            action_taken = verb
            note_written = True
        else:
            action_taken = "note_queued_for_approval"

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_COMMUNICATION, trigger_event_type="hrm.note_update_requested",
        trigger_context={"employee_id": employee_id, "context": context},
        reasoning=reasoning, tools_called=tools_called, action_taken=action_taken, outcome="success" if note_written else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_COMMUNICATION, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return NoteUpdateResult(decision_log=log, note_written=note_written, action_taken=action_taken, reasoning=reasoning)


async def handle_note_update_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> NoteUpdateResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_note_update(db, tenant_id, payload.get("employee_id", ""), payload.get("context", ""))


async def handle_case_resolved(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> NoteUpdateResult:
    """Adapter for hrm.case_resolved — logs a factual note that the
    case was resolved, using the same note-update capability."""
    context = f"Case resolved — {payload.get('case_type', 'case')}: {payload.get('subject', '')}"
    return await evaluate_note_update(db, tenant_id, payload.get("employee_id", ""), context)


EMAIL_DRAFT_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, \
drafting a formal email from a short administrator prompt (HRM-008).

Expand the short prompt into a properly formatted, professional email — a real subject \
line and a body with a greeting and sign-off, not just the prompt restated. You are \
never sending this yourself; an administrator always reviews and can edit before it's \
dispatched, whether it's going to all staff or one person. Always finish by calling \
submit_email_draft."""


def _deterministic_email_draft(prompt: str, recipient_scope: str) -> tuple[str, str]:
    """A real, honest template shell — this wraps the administrator's
    own words in a formal structure rather than attempting to
    genuinely understand and rewrite them, which only the live-Claude
    path can meaningfully do. Never invents content beyond the prompt
    itself."""
    greeting = "Dear Team," if recipient_scope == "all_staff" else "Dear Colleague,"
    subject = prompt.strip().rstrip(".!?")
    subject = subject[:1].upper() + subject[1:] if subject else "VOYQI Update"
    body = f"{greeting}\n\n{prompt.strip()}\n\nBest regards,\nVOYQI HR"
    return subject, body


@dataclass
class EmailDraftResult:
    decision_log: DecisionLog
    email_created: bool
    reasoning: str
    skipped_reason: str | None = None
    email_sent: bool = False


async def evaluate_email_draft(
    db: AsyncSession,
    tenant_id: str,
    recipient_scope: str,
    prompt: str,
    recipient_employee_id: str | None = None,
    client: AnthropicAgentClient | None = None,
) -> EmailDraftResult:
    """HRM-008 — runs automatically once an administrator requests it.
    Full automation: creates the draft and immediately sends it — no
    financial or security risk in dispatching a formal communication
    itself."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_COMMUNICATION)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_COMMUNICATION, trigger_event_type="hrm.email_draft_requested",
            trigger_context={"recipient_scope": recipient_scope, "prompt": prompt},
            reasoning=f"Agent status is {status.value} — no email was drafted.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return EmailDraftResult(decision_log=log, email_created=False, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = hrm_tools.build_email_draft_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=EMAIL_DRAFT_SYSTEM_PROMPT,
            user_message=f"Prompt: '{prompt}'. Recipient scope: {recipient_scope}. Draft a formal email.",
            tools=hrm_tools.EMAIL_DRAFT_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_email_draft"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            subject = submitted.get("subject", "")
            body = submitted.get("body", "")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            subject, body = "", ""
            reasoning = turn.final_text or "(agent produced no final text and never called submit_email_draft)"
    else:
        subject, body = _deterministic_email_draft(prompt, recipient_scope)
        reasoning = "[deterministic fallback — ANTHROPIC_API_KEY not configured] Prompt wrapped in a formal email shell, not rewritten — genuine expansion needs the live model."

    email_created = False
    email_sent = False
    if subject and body:
        created = hrm_tools.create_email_draft(tenant_id, recipient_scope, subject, body, recipient_employee_id, prompt)
        tools_called.append({"tool": "create_email_draft", "input": {"recipient_scope": recipient_scope}})
        email_created = True
        if created.get("id"):
            gated = await _hrm_gate(
                db, tenant_id, AGENT_KEY_COMMUNICATION, "email.send", "hrm.send_email",
                {"email_id": created["id"]}, reasoning, "hrm.email_draft_requested",
            )
            if gated.executed:
                tools_called.append({"tool": "send_email", "input": {"email_id": created["id"]}})
                email_sent = bool(gated.result)

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_COMMUNICATION, trigger_event_type="hrm.email_draft_requested",
        trigger_context={"recipient_scope": recipient_scope, "prompt": prompt, "recipient_employee_id": recipient_employee_id},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="email_draft_created_and_sent" if email_sent else ("email_draft_created" if email_created else "no_draft_created"),
        outcome="success" if email_created else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_COMMUNICATION, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return EmailDraftResult(decision_log=log, email_created=email_created, reasoning=reasoning, email_sent=email_sent)


async def handle_email_draft_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> EmailDraftResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_email_draft(db, tenant_id, payload.get("recipient_scope", ""), payload.get("prompt", ""), payload.get("recipient_employee_id"))


COURSE_DRAFT_SYSTEM_PROMPT = """You are the Talent Agent for VOYQI's AI-Native HRM, \
helping prepare a new VOYQI Academy course from a short HR prompt (HRM-006/HRM-008).

Draft a clear title, pick the best-fitting category (compliance, safety, technical, or \
leadership), write a short description, and suggest a content type (video, document, \
quiz, or in_person) based on what would genuinely suit the topic — a hands-on safety \
topic usually suits video better than a compliance policy read, for instance. This is \
only ever a draft: an administrator always reviews it, can edit any part, and decides \
whether it's mandatory before it becomes a real course in the catalog. Always finish by \
calling submit_course_draft."""

_CATEGORY_KEYWORDS = {
    "compliance": ["compliance", "harassment", "policy", "legal", "regulation"],
    "safety": ["safety", "hazard", "driving", "forklift", "osha", "injury"],
    "leadership": ["leadership", "management", "coaching", "mentoring"],
}


def _deterministic_course_draft(prompt: str) -> tuple[str, str, str, str]:
    """A real, auditable keyword-based category and content-type
    inference — the same principle as HRM-003's resume screening.
    Falls back to technical/document when nothing matches, rather
    than guessing confidently."""
    prompt_lower = prompt.lower()
    category = "technical"
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in prompt_lower for kw in keywords):
            category = cat
            break

    content_type = "video" if category == "safety" else "document"
    title = prompt.strip()[:1].upper() + prompt.strip()[1:] if prompt.strip() else "New Course"
    description = f"Course covering: {prompt.strip()}."
    return title, category, description, content_type


@dataclass
class CourseDraftResult:
    decision_log: DecisionLog
    draft_created: bool
    reasoning: str
    skipped_reason: str | None = None
    draft_published: bool = False


async def evaluate_course_draft(
    db: AsyncSession,
    tenant_id: str,
    prompt: str,
    client: AnthropicAgentClient | None = None,
) -> CourseDraftResult:
    """HRM-006/HRM-008 — AI-assisted course prep. Full automation:
    creates the draft and immediately publishes it to the catalog —
    catalog curation carries no financial or security risk on its
    own. is_mandatory defaults to False on publish, so nothing
    becomes a required course without a person explicitly setting
    that later."""
    status = await _agent_status(db, tenant_id, AGENT_KEY_ACADEMY)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY_ACADEMY, trigger_event_type="hrm.course_draft_requested",
            trigger_context={"prompt": prompt},
            reasoning=f"Agent status is {status.value} — no course draft was created.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return CourseDraftResult(decision_log=log, draft_created=False, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = hrm_tools.build_course_draft_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=COURSE_DRAFT_SYSTEM_PROMPT,
            user_message=f"Prompt: '{prompt}'. Draft a course.",
            tools=hrm_tools.COURSE_DRAFT_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_course_draft"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            title = submitted.get("title", "")
            category = submitted.get("category", "technical")
            description = submitted.get("description", "")
            content_type = submitted.get("content_type", "document")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            title, category, description, content_type = "", "technical", "", "document"
            reasoning = turn.final_text or "(agent produced no final text and never called submit_course_draft)"
    else:
        title, category, description, content_type = _deterministic_course_draft(prompt)
        reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Category inferred from keyword match ('{category}'); content type defaults to '{content_type}' for this category."

    draft_created = False
    draft_published = False
    if title:
        created = hrm_tools.create_course_draft(tenant_id, prompt, title, category, description, content_type, reasoning)
        tools_called.append({"tool": "create_course_draft", "input": {"title": title, "category": category}})
        draft_created = True
        if created.get("id"):
            gated = await _hrm_gate(
                db, tenant_id, AGENT_KEY_ACADEMY, "course.publish", "hrm.publish_course_draft",
                {"draft_id": created["id"]}, reasoning, "hrm.course_draft_requested",
            )
            if gated.executed:
                tools_called.append({"tool": "publish_course_draft", "input": {"draft_id": created["id"]}})
                draft_published = bool(gated.result)

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY_ACADEMY, trigger_event_type="hrm.course_draft_requested",
        trigger_context={"prompt": prompt},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="course_draft_created_and_published" if draft_published else ("course_draft_created" if draft_created else "no_draft_created"),
        outcome="success" if draft_created else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY_ACADEMY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return CourseDraftResult(decision_log=log, draft_created=draft_created, reasoning=reasoning, draft_published=draft_published)


async def handle_course_draft_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> CourseDraftResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_course_draft(db, tenant_id, payload.get("prompt", ""))
