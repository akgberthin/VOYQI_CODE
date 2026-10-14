"""
Dealer Intelligence Agent — orchestration layer for the DMS
capabilities NOT already covered by the Pricing Intelligence Agent
(which owns trade appraisal and retail pricing; see
pricing_intelligence_agent.py).

The decision logic itself — aging thresholds, FICO tiers, gross-floor
math, reorder points, license-renewal windows, remediation triggers,
the P&L capstone's cross-signal scoring — lives in dms-svc's own
engines, deterministically, the same way fleet-svc owns its covenant
and aging math. This agent's job is orchestration in the literal
sense: invoking the right dms-svc capability at the right trigger
event and writing the immutable DecisionLog audit row that makes the
resulting action traceable — input -> action -> outcome — the same
guarantee every other agent on this platform provides.

Every function here is fully autonomous. The three genuine security
matters on this platform — OFAC sanctions, Red Flags Rule identity-
theft indicators, and stolen-vehicle registry holds — are enforced
inside dms-svc itself, not here; this agent has no code path around
any of them, the same way fleet_intelligence_agent.py has no path to
liability_assessment or covenant unfreezing.
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.tiering import get_tier
from src.core import action_gate
from src.models import Agent, AgentStatus, AIRecommendation, DecisionLog, RecommendationStatus
from src.services import dms_tools

AGENT_KEY = "dealer_intelligence_agent"

# The tier-policy agent key for credit decisions is
# "sales-originations-intelligence" (docs/AUTONOMY_TIERING_ENGINEER_
# INSTRUCTIONS.md §4) — a separate namespace from this file's own
# AGENT_KEY, which identifies the *agent process* for status/audit
# tracking. The two don't have to match: one action can belong to a
# different policy domain than the code module that triggers it.
CREDIT_DECISION_POLICY_AGENT = "sales-originations-intelligence"
CREDIT_DECISION_ACTION_TYPE = "credit.decision"


async def _agent_status(db: AsyncSession, tenant_id: str) -> AgentStatus:
    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    return agent_row.status if agent_row else AgentStatus.ACTIVE


@dataclass
class DealerOrchestrationResult:
    decision_log: DecisionLog
    output: Any
    skipped_reason: str | None = None


DEALER_OPS_POLICY_AGENT = "dealer-operations"


async def _run(
    db: AsyncSession, tenant_id: str, trigger_event_type: str, trigger_context: dict,
    action_taken: str, reasoning: str, *, policy_action: str, executor: str, args: dict,
    policy_agent: str = DEALER_OPS_POLICY_AGENT,
) -> DealerOrchestrationResult:
    """Shared orchestration shell: check agent status, run the dms-svc
    capability through the autonomy gate (tier 1 executes now; tier 2/3
    queues an AIRecommendation that executes on approval), write the
    DecisionLog, increment the agent's decision counter."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type=trigger_event_type,
            trigger_context=trigger_context, reasoning=f"Agent status is {status.value} — no action was taken.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return DealerOrchestrationResult(decision_log=log, output=None, skipped_reason=status.value)

    gated = await action_gate.gate(
        db, tenant_id=tenant_id, agent_key=AGENT_KEY, policy_agent=policy_agent, action_type=policy_action,
        executor=executor, args=args, reasoning=reasoning, triggering_event_type=trigger_event_type, domain="dms",
        context=trigger_context,
    )
    output = gated.result

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type=trigger_event_type,
        trigger_context=trigger_context, reasoning=reasoning, tools_called=[{"tool": action_taken, "input": trigger_context}] if gated.executed else [],
        action_taken=action_taken if gated.executed else f"{policy_action}_queued_for_approval",
        outcome="success" if gated.executed else "deferred",
        recommendation_id=gated.recommendation.id if gated.recommendation else None,
        tier=gated.tier, approval_required=not gated.executed,
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return DealerOrchestrationResult(decision_log=log, output=output)


# ══════════════════════════════════════════════════════════════
# DMS-001 — Rooftop license renewal
# ══════════════════════════════════════════════════════════════
async def evaluate_license_renewals(db: AsyncSession, tenant_id: str) -> DealerOrchestrationResult:
    return await _run(
        db, tenant_id, "rooftop.renewal_sweep_requested", {},
        "license_renewals_filed",
        "[deterministic] Swept every rooftop for licenses within the renewal window and filed autonomously — a plain date comparison, not a judgment call.",
        policy_action="rooftop.license_renewal_file", executor="dms.evaluate_license_renewals", args={},
    )


# ══════════════════════════════════════════════════════════════
# DMS-002 — Retail inventory aging sweep (batch; the per-vehicle
# recommendation capability lives in pricing_intelligence_agent.py)
# ══════════════════════════════════════════════════════════════
async def evaluate_aging_sweep(db: AsyncSession, tenant_id: str) -> DealerOrchestrationResult:
    return await _run(
        db, tenant_id, "inventory.aging_sweep_requested", {},
        "aging_markdowns_applied",
        "[deterministic] Swept in-stock inventory for units past the aging threshold and applied markdowns autonomously, anchored on book value where available.",
        policy_action="inventory.aging_markdown_apply", executor="dms.evaluate_aging_sweep", args={},
    )


# ══════════════════════════════════════════════════════════════
# DMS-003 — Sales desk: desk and approve in one call
# ══════════════════════════════════════════════════════════════
async def evaluate_deal_desk(db: AsyncSession, tenant_id: str, deal_id: str) -> DealerOrchestrationResult:
    return await _run(
        db, tenant_id, "deal.desk_requested", {"deal_id": deal_id},
        "deal_desked_and_approved",
        "[deterministic] Computed the desk price and checked it against the minimum gross floor; a price missing the floor is auto-countered upward and approved in the same pass — never queued for a manager.",
        policy_agent=CREDIT_DECISION_POLICY_AGENT, policy_action="deal.structure_recommend",
        executor="dms.desk_and_approve_deal", args={"deal_id": deal_id},
    )


# ══════════════════════════════════════════════════════════════
# DMS-004 — Lending. OFAC is a structural block inside dms-svc
# itself, unrelated to tiering — this wrapper has no path around
# it and that check still runs first, unconditionally.
#
# The actual bureau-pull -> route -> decision -> winner chain is a
# credit decision — tier 3, hard-coded, no exceptions
# (docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §4: "credit
# decisions are tier 3, hard-coded, no exceptions — this is a
# fair-lending requirement"). It no longer auto-executes: a pending
# AIRecommendation is created instead, and dms_tools.pull_bureau_and_route
# only runs once a human with a named accountable role approves it
# via POST /api/v1/ai/recommendations/{id}/approve — see
# src/api/recommendations.py.
# ══════════════════════════════════════════════════════════════
async def evaluate_lending(db: AsyncSession, tenant_id: str, app_id: str, bureau: str, fico: int, ofac_match: bool) -> DealerOrchestrationResult:
    if ofac_match:
        return await _run(
            db, tenant_id, "lending.bureau_pull_requested",
            {"app_id": app_id, "bureau": bureau, "fico": fico, "ofac_match": ofac_match},
            "credit_application_blocked",
            "[security] OFAC sanctions match — application permanently blocked before any lender routing was "
            "attempted. No override exists, here or anywhere downstream.",
            policy_action="credit.ofac_block_apply", executor="dms.pull_bureau_and_route",
            args={"app_id": app_id, "bureau": bureau, "fico": fico, "ofac_match": ofac_match},
        )

    status = await _agent_status(db, tenant_id)
    trigger_context = {"app_id": app_id, "bureau": bureau, "fico": fico, "ofac_match": ofac_match}
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="lending.bureau_pull_requested",
            trigger_context=trigger_context, reasoning=f"Agent status is {status.value} — no action was taken.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return DealerOrchestrationResult(decision_log=log, output=None, skipped_reason=status.value)

    tier = get_tier(CREDIT_DECISION_POLICY_AGENT, CREDIT_DECISION_ACTION_TYPE, trigger_context)

    recommendation = AIRecommendation(
        tenant_id=tenant_id, agent_key=AGENT_KEY, domain="dms", action_type="credit_decision_pending_human_review",
        proposed_action={"app_id": app_id, "bureau": bureau, "fico": fico, "ofac_match": ofac_match},
        reasoning=(
            f"OFAC cleared. Bureau pull, lender routing, and offer decisioning are a credit decision "
            f"(tier {tier}) — this platform does not auto-decision credit applications regardless of FICO or "
            f"model confidence. A named accountable role must approve before dms_tools.pull_bureau_and_route runs."
        ),
        risk_score=100.0, drivers=["credit_decision", "fair_lending_tier_3"],
        external_record_id=app_id, status=RecommendationStatus.PENDING,
        triggering_event_type="lending.bureau_pull_requested", triggering_event_id=app_id,
        tier=tier, requires_accountable_role=tier >= 3,
    )
    db.add(recommendation)
    await db.flush()

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, recommendation_id=recommendation.id,
        trigger_event_type="lending.bureau_pull_requested", trigger_context=trigger_context,
        reasoning=recommendation.reasoning, tools_called=[],
        action_taken="credit_decision_pending_human_review", outcome="deferred",
        tier=tier, approval_required=True,
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1
        agent_row.recommendations_pending += 1

    await db.commit()
    await db.refresh(log)
    return DealerOrchestrationResult(decision_log=log, output=None)


# ══════════════════════════════════════════════════════════════
# DMS-006 — Service: dispatch on RO creation
# ══════════════════════════════════════════════════════════════
async def evaluate_service_dispatch(db: AsyncSession, tenant_id: str, rooftop_id: str, vin: str, customer_name: str, description: str, warranty: bool) -> DealerOrchestrationResult:
    return await _run(
        db, tenant_id, "service.repair_order_opened", {"rooftop_id": rooftop_id, "vin": vin, "description": description, "warranty": warranty},
        "repair_order_dispatched",
        "[deterministic] Assigned a tech and estimated job duration from description keywords; a multi-day estimate auto-chains into a loaner assignment.",
        policy_action="service.repair_order_dispatch", executor="dms.dispatch_repair_order",
        args={"rooftop_id": rooftop_id, "vin": vin, "customer_name": customer_name, "description": description, "warranty": warranty},
    )


# ══════════════════════════════════════════════════════════════
# DMS-007 — Parts vendor reordering (batch sweep)
# ══════════════════════════════════════════════════════════════
async def evaluate_parts_reorder(db: AsyncSession, tenant_id: str) -> DealerOrchestrationResult:
    return await _run(
        db, tenant_id, "parts.reorder_sweep_requested", {},
        "vendor_orders_placed",
        "[deterministic] Swept parts stock for anything at or below its reorder point and placed vendor orders autonomously.",
        policy_action="parts.vendor_order_place", executor="dms.evaluate_parts_reorder", args={},
    )


# ══════════════════════════════════════════════════════════════
# DMS-008 — Loaner overdue billing flag (batch sweep)
# ══════════════════════════════════════════════════════════════
async def evaluate_loaner_overdue(db: AsyncSession, tenant_id: str) -> DealerOrchestrationResult:
    return await _run(
        db, tenant_id, "loaner.overdue_sweep_requested", {},
        "extended_loaners_flagged",
        "[deterministic] Swept active loaners for anything past the extended-duration threshold and flagged for billing handoff autonomously.",
        policy_action="loaner.overdue_flag", executor="dms.evaluate_loaner_overdue", args={},
    )


# ══════════════════════════════════════════════════════════════
# DMS-010 — Franchise remediation (batch sweep)
# ══════════════════════════════════════════════════════════════
async def evaluate_franchise_remediation(db: AsyncSession, tenant_id: str) -> DealerOrchestrationResult:
    return await _run(
        db, tenant_id, "franchise.remediation_sweep_requested", {},
        "remediation_tasks_opened",
        "[deterministic] Swept franchise compliance records for anything non-compliant and opened remediation tasks autonomously.",
        policy_action="franchise.remediation_task_open", executor="dms.evaluate_franchise_remediation", args={},
    )


# ══════════════════════════════════════════════════════════════
# DMS-009 — P&L capstone: cross-signal verdict + accelerated
# markdown intervention, the highest-leverage capability in this
# agent — reads real transaction arithmetic from PLSnapshot and
# FranchiseCompliance, never writes to either, and acts on a
# DIFFERENT table (Vehicle pricing) when a rooftop is weak.
# ══════════════════════════════════════════════════════════════
async def evaluate_dealer_performance(db: AsyncSession, tenant_id: str) -> DealerOrchestrationResult:
    return await _run(
        db, tenant_id, "performance.evaluation_requested", {},
        "performance_evaluated_and_intervened",
        "[deterministic] Scored every rooftop on gross, turn, absorption, and franchise compliance against group "
        "averages and fixed thresholds; any rooftop verdicted 'intervene' had an accelerated inventory markdown "
        "sweep applied autonomously — a cross-module action, not just a dashboard label.",
        policy_action="performance.intervention_markdown_apply", executor="dms.evaluate_dealer_performance", args={},
    )
