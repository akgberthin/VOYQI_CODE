import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import action_gate
from src.core.database import get_db
from src.core.security import get_current_actor, get_tenant_id
from src.models import AIRecommendation, DecisionLog, RecommendationStatus
from src.services import dms_tools, identity_tools, rbac_tools

router = APIRouter(prefix="/api/v1/ai", tags=["recommendations"])


class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_key: str
    domain: str
    action_type: str
    proposed_action: dict
    reasoning: str
    risk_score: float
    drivers: list
    status: RecommendationStatus
    decided_by: str | None
    decision_reason_code: str | None
    triggering_event_type: str
    tier: int | None
    requires_accountable_role: bool
    accountable_role: str | None


class DecisionRequest(BaseModel):
    reason_code: str
    note: str = ""
    # Tier 3 only (docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §1.2):
    # the UI and the audit record must capture *who* is accountable, not
    # just that a human clicked something. Required to approve any
    # recommendation with requires_accountable_role=True; ignored
    # otherwise.
    accountable_role: str | None = None


@router.get("/recommendations", response_model=list[RecommendationOut])
async def list_recommendations(
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    status: RecommendationStatus | None = None,
):
    query = select(AIRecommendation).where(AIRecommendation.tenant_id == tenant_id)
    if status is not None:
        query = query.where(AIRecommendation.status == status)
    result = await db.execute(query.order_by(AIRecommendation.created_at.desc()))
    return result.scalars().all()


async def _get_recommendation(db, tenant_id, recommendation_id) -> AIRecommendation:
    result = await db.execute(
        select(AIRecommendation).where(
            AIRecommendation.id == recommendation_id, AIRecommendation.tenant_id == tenant_id
        )
    )
    rec = result.scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return rec


@router.post("/recommendations/{recommendation_id}/approve", response_model=RecommendationOut)
async def approve_recommendation(
    recommendation_id: uuid.UUID,
    body: DecisionRequest,
    tenant_id: str = Depends(get_tenant_id),
    actor: str = Depends(get_current_actor),
    db: AsyncSession = Depends(get_db),
):
    rec = await _get_recommendation(db, tenant_id, recommendation_id)
    if rec.status != RecommendationStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Recommendation already {rec.status.value}")

    # Tier 3 hard block — not a UI convention, a structural requirement.
    # This is the only place in the codebase that can approve a
    # requires_accountable_role recommendation, and it refuses to do so
    # without a named role, full stop. See
    # docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §1.2.
    if rec.requires_accountable_role and not body.accountable_role:
        raise HTTPException(
            status_code=400,
            detail="accountable_role is required to approve a tier-3 recommendation",
        )

    if (rec.proposed_action or {}).get("executor"):
        # Queued by core/action_gate.gate() because the tier policy put
        # this action at tier 2/3 — the action itself runs only now.
        action_gate.execute_approved(tenant_id, rec.proposed_action)
    elif rec.domain == "dms" and rec.action_type == "credit_decision_pending_human_review":
        # The credit decision itself runs only now, on approval — see
        # dealer_intelligence_agent.evaluate_lending, which creates this
        # recommendation instead of auto-executing.
        proposed = rec.proposed_action
        dms_tools.pull_bureau_and_route(
            tenant_id, proposed["app_id"], proposed["bureau"], proposed["fico"], proposed["ofac_match"]
        )
    elif rec.domain == "rbac" and rec.action_type == "review_permission_grant" and rec.external_record_id:
        # Approving here means "the agent's revoke recommendation is
        # correct" — actually execute it against rbac-svc.
        rbac_tools.revoke_permission_override(
            tenant_id, rec.external_record_id, reason=f"AI-002 approved: {body.note or body.reason_code}"
        )
    elif rec.domain == "identity" and rec.action_type == "review_break_glass_session" and rec.external_record_id:
        identity_tools.end_break_glass_session(
            tenant_id, rec.external_record_id, reason=f"AI-002 approved: {body.note or body.reason_code}"
        )
    elif rec.domain == "identity" and rec.action_type == "review_impersonation_session" and rec.external_record_id:
        identity_tools.end_impersonation_session(
            tenant_id, rec.external_record_id, reason=f"AI-002 approved: {body.note or body.reason_code}"
        )

    rec.status = RecommendationStatus.APPROVED
    rec.decided_by = actor
    rec.decision_reason_code = body.reason_code
    rec.decided_at = datetime.now(timezone.utc)
    if body.accountable_role:
        rec.accountable_role = body.accountable_role

    db.add(
        DecisionLog(
            tenant_id=tenant_id,
            agent_key=rec.agent_key,
            recommendation_id=rec.id,
            trigger_event_type=rec.triggering_event_type,
            trigger_context=rec.proposed_action,
            reasoning=(
                f"Human override by {actor}"
                + (f" (accountable role: {body.accountable_role})" if body.accountable_role else "")
                + f": approved. {body.note}"
            ),
            tools_called=[],
            action_taken="human_approved_and_executed",
            outcome="success",
            is_override=True,
            tier=rec.tier,
            approval_required=True,
        )
    )
    await db.commit()
    await db.refresh(rec)
    return rec


@router.post("/recommendations/{recommendation_id}/reject", response_model=RecommendationOut)
async def reject_recommendation(
    recommendation_id: uuid.UUID,
    body: DecisionRequest,
    tenant_id: str = Depends(get_tenant_id),
    actor: str = Depends(get_current_actor),
    db: AsyncSession = Depends(get_db),
):
    rec = await _get_recommendation(db, tenant_id, recommendation_id)
    if rec.status != RecommendationStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Recommendation already {rec.status.value}")

    rec.status = RecommendationStatus.REJECTED
    rec.decided_by = actor
    rec.decision_reason_code = body.reason_code
    rec.decided_at = datetime.now(timezone.utc)

    db.add(
        DecisionLog(
            tenant_id=tenant_id,
            agent_key=rec.agent_key,
            recommendation_id=rec.id,
            trigger_event_type=rec.triggering_event_type,
            trigger_context=rec.proposed_action,
            reasoning=f"Human override by {actor}: rejected. {body.note}",
            tools_called=[],
            action_taken="human_rejected_no_action",
            outcome="success",
            is_override=True,
        )
    )
    await db.commit()
    await db.refresh(rec)
    return rec
