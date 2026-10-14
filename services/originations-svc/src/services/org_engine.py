"""ORG-001..003 — unified origination pipeline.

Every stage publishes the event the Revenue Agent (or, on
finalization, the Compliance/Audit Agent) is routed on:

  create_case            -> org.case_created              (agent pre-qualifies, tier 1)
  request_decisioning    -> org.decisioning_requested     (agent risk-scores, tier 1)
  finalize_decision      -> org.decision_finalized        (fair-lending disparate-impact check)
                            org.funding_match_requested   (approved only; agent matches a lender, tier 1)

finalize_decision is the tier-3 credit.decision gate: no approve or
decline without a named accountable_role, and never before the case has
been scored against the same fixed bar as every other case.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.kafka_events import publish_event
from src.models import (
    OrgDecisionStatus,
    OrgFundingSource,
    OriginationCase,
    OriginationCaseStatus,
    OriginationDecision,
    OriginationType,
    RecommendedOutcome,
)

PREQUAL_VERDICT_STATUS = {
    "qualified": OriginationCaseStatus.PREQUALIFIED,
    "needs_review": OriginationCaseStatus.NEEDS_REVIEW,
    "incomplete": OriginationCaseStatus.INCOMPLETE,
}


class CaseNotFoundError(Exception):
    pass


class DecisionNotFoundError(Exception):
    pass


class InvalidRiskScoreError(Exception):
    pass


class NotScoredError(Exception):
    pass


class AlreadyDecidedError(Exception):
    pass


class AccountableRoleRequiredError(Exception):
    """Raised for an approval and a decline alike — an adverse action
    needs accountable documentation as much as an approval does."""


class OpenDecisionExistsError(Exception):
    pass


# --- cases -----------------------------------------------------------------


async def create_case(
    db: AsyncSession, tenant_id: str, origination_type: OriginationType, full_name: str, amount_usd: float,
    email: str | None = None, phone: str | None = None, external_ref_id: str | None = None,
) -> OriginationCase:
    case = OriginationCase(
        tenant_id=tenant_id, origination_type=origination_type, full_name=full_name, amount_usd=amount_usd,
        email=email, phone=phone, external_ref_id=external_ref_id,
    )
    db.add(case)
    await db.commit()
    await db.refresh(case)
    await publish_event(
        "org.case_created",
        {
            "tenant_id": tenant_id, "case_id": str(case.id), "origination_type": case.origination_type.value,
            "full_name": full_name, "external_ref_id": external_ref_id or "",
        },
    )
    return case


async def get_case(db: AsyncSession, tenant_id: str, case_id: uuid.UUID) -> OriginationCase | None:
    result = await db.execute(select(OriginationCase).where(OriginationCase.id == case_id, OriginationCase.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_cases(db: AsyncSession, tenant_id: str, status: OriginationCaseStatus | None = None) -> list[OriginationCase]:
    query = select(OriginationCase).where(OriginationCase.tenant_id == tenant_id)
    if status is not None:
        query = query.where(OriginationCase.status == status)
    result = await db.execute(query.order_by(OriginationCase.created_at.desc()))
    return list(result.scalars().all())


async def set_prequalification(
    db: AsyncSession, tenant_id: str, case_id: uuid.UUID, verdict: str, reasoning: str, next_step_guidance: str,
) -> OriginationCase:
    """The Revenue Agent's write-back (tier 1). Only moves a case that
    hasn't progressed past pre-qualification — a late or replayed
    verdict never rewinds a case already in decisioning."""
    case = await get_case(db, tenant_id, case_id)
    if case is None:
        raise CaseNotFoundError(f"case {case_id} not found")
    case.prequal_verdict = verdict
    case.prequal_reasoning = reasoning
    case.prequal_next_step = next_step_guidance
    if case.status in (OriginationCaseStatus.OPEN, *PREQUAL_VERDICT_STATUS.values()):
        case.status = PREQUAL_VERDICT_STATUS.get(verdict, OriginationCaseStatus.NEEDS_REVIEW)
    await db.commit()
    await db.refresh(case)
    return case


# --- decisions -------------------------------------------------------------


async def request_decisioning(db: AsyncSession, tenant_id: str, case_id: uuid.UUID) -> OriginationDecision:
    case = await get_case(db, tenant_id, case_id)
    if case is None:
        raise CaseNotFoundError(f"case {case_id} not found")
    existing = await db.execute(
        select(OriginationDecision).where(
            OriginationDecision.case_id == case_id,
            OriginationDecision.tenant_id == tenant_id,
            OriginationDecision.status.in_([OrgDecisionStatus.AWAITING_SCORE, OrgDecisionStatus.SCORED]),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise OpenDecisionExistsError(f"case {case_id} already has an open decision")

    decision = OriginationDecision(tenant_id=tenant_id, case_id=case_id)
    case.status = OriginationCaseStatus.IN_DECISIONING
    db.add(decision)
    await db.commit()
    await db.refresh(decision)
    await publish_event(
        "org.decisioning_requested",
        {
            "tenant_id": tenant_id, "decision_id": str(decision.id), "case_id": str(case_id),
            "origination_type": case.origination_type.value, "full_name": case.full_name,
            "external_ref_id": case.external_ref_id or "",
        },
    )
    return decision


async def get_decision(db: AsyncSession, tenant_id: str, decision_id: uuid.UUID) -> OriginationDecision | None:
    result = await db.execute(
        select(OriginationDecision).where(OriginationDecision.id == decision_id, OriginationDecision.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def list_decisions(db: AsyncSession, tenant_id: str, status: OrgDecisionStatus | None = None) -> list[OriginationDecision]:
    query = select(OriginationDecision).where(OriginationDecision.tenant_id == tenant_id)
    if status is not None:
        query = query.where(OriginationDecision.status == status)
    result = await db.execute(query.order_by(OriginationDecision.created_at.desc()))
    return list(result.scalars().all())


def recommend_outcome(risk_score: float) -> RecommendedOutcome:
    settings = get_settings()
    if risk_score >= settings.org_approve_risk_score:
        return RecommendedOutcome.APPROVE
    if risk_score < settings.org_decline_risk_score:
        return RecommendedOutcome.DECLINE
    return RecommendedOutcome.MANUAL_REVIEW


async def apply_score(db: AsyncSession, tenant_id: str, decision_id: uuid.UUID, risk_score: float, reasoning: str) -> OriginationDecision:
    """The Revenue Agent's write-back (tier 1). Scoring is not deciding:
    it only records the score and the threshold's recommendation."""
    if not 0.0 <= risk_score <= 1.0:
        raise InvalidRiskScoreError("risk_score must be between 0.0 and 1.0")
    decision = await get_decision(db, tenant_id, decision_id)
    if decision is None:
        raise DecisionNotFoundError(f"decision {decision_id} not found")
    if decision.status in (OrgDecisionStatus.APPROVED, OrgDecisionStatus.DECLINED):
        raise AlreadyDecidedError(f"decision {decision_id} is already {decision.status.value}")
    decision.risk_score = risk_score
    decision.score_reasoning = reasoning
    decision.recommended_outcome = recommend_outcome(risk_score)
    decision.status = OrgDecisionStatus.SCORED
    await db.commit()
    await db.refresh(decision)
    return decision


async def finalize_decision(
    db: AsyncSession, tenant_id: str, decision_id: uuid.UUID, approve: bool, accountable_role: str | None,
    decided_by: str, reasoning: str = "",
) -> OriginationDecision:
    """Tier-3 credit.decision gate — hard-coded, no threshold, no AI path."""
    decision = await get_decision(db, tenant_id, decision_id)
    if decision is None:
        raise DecisionNotFoundError(f"decision {decision_id} not found")
    if decision.status in (OrgDecisionStatus.APPROVED, OrgDecisionStatus.DECLINED):
        raise AlreadyDecidedError(f"decision {decision_id} is already {decision.status.value}")
    if decision.status != OrgDecisionStatus.SCORED:
        raise NotScoredError("a decision cannot be finalized before it has been risk-scored")
    if not accountable_role or not accountable_role.strip():
        raise AccountableRoleRequiredError("accountable_role is required to finalize an origination decision")

    case = await get_case(db, tenant_id, decision.case_id)
    decision.status = OrgDecisionStatus.APPROVED if approve else OrgDecisionStatus.DECLINED
    decision.accountable_role = accountable_role.strip()
    decision.decided_by = decided_by
    decision.decision_reasoning = reasoning
    decision.decided_at = datetime.now(timezone.utc)
    case.status = OriginationCaseStatus.APPROVED if approve else OriginationCaseStatus.DECLINED
    await db.commit()
    await db.refresh(decision)

    await publish_event(
        "org.decision_finalized",
        {
            "tenant_id": tenant_id, "decision_id": str(decision.id), "case_id": str(case.id),
            "origination_type": case.origination_type.value, "outcome": decision.status.value,
            "followed_recommendation": decision.recommended_outcome is not None
            and decision.recommended_outcome.value == ("approve" if approve else "decline"),
            "accountable_role": decision.accountable_role,
        },
    )
    if approve:
        await publish_event(
            "org.funding_match_requested",
            {
                "tenant_id": tenant_id, "case_id": str(case.id), "decision_id": str(decision.id),
                "origination_type": case.origination_type.value, "risk_score": decision.risk_score,
                "amount_usd": case.amount_usd,
            },
        )
    return decision


async def disparate_impact_snapshot(db: AsyncSession, tenant_id: str) -> dict:
    """Approval/decline rates by origination type — business variables
    only; no protected-class data exists in this system."""
    result = await db.execute(
        select(OriginationDecision.status, OriginationCase.origination_type)
        .join(OriginationCase, OriginationCase.id == OriginationDecision.case_id)
        .where(
            OriginationDecision.tenant_id == tenant_id,
            OriginationDecision.status.in_([OrgDecisionStatus.APPROVED, OrgDecisionStatus.DECLINED]),
        )
    )
    counts: dict[str, dict[str, int]] = {}
    for status, origination_type in result.all():
        group = counts.setdefault(origination_type.value, {"approved": 0, "declined": 0})
        group["approved" if status == OrgDecisionStatus.APPROVED else "declined"] += 1
    groups = []
    for origination_type, c in sorted(counts.items()):
        total = c["approved"] + c["declined"]
        groups.append({
            "origination_type": origination_type, "total": total, "approved": c["approved"], "declined": c["declined"],
            "decline_rate_pct": round(100.0 * c["declined"] / total, 1) if total else 0.0,
        })
    return {"groups": groups}


# --- funding sources -------------------------------------------------------


async def create_funding_source(
    db: AsyncSession, tenant_id: str, name: str, applicable_origination_type: OriginationType,
    min_risk_score: float, max_amount_usd: float, reserve_participation_pct: float,
) -> OrgFundingSource:
    source = OrgFundingSource(
        tenant_id=tenant_id, name=name, applicable_origination_type=applicable_origination_type,
        min_risk_score=min_risk_score, max_amount_usd=max_amount_usd, reserve_participation_pct=reserve_participation_pct,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def list_funding_sources(
    db: AsyncSession, tenant_id: str, origination_type: OriginationType | None = None, active_only: bool = True,
) -> list[OrgFundingSource]:
    query = select(OrgFundingSource).where(OrgFundingSource.tenant_id == tenant_id)
    if origination_type is not None:
        query = query.where(OrgFundingSource.applicable_origination_type == origination_type)
    if active_only:
        query = query.where(OrgFundingSource.active.is_(True))
    result = await db.execute(query.order_by(OrgFundingSource.name))
    return list(result.scalars().all())
