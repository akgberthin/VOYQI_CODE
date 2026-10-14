import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import AIRecommendation, DecisionLog, RecommendationStatus

router = APIRouter(prefix="/api/v1/ai", tags=["audit"])


class DecisionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_key: str
    recommendation_id: uuid.UUID | None
    trigger_event_type: str
    trigger_context: dict
    reasoning: str
    tools_called: list
    action_taken: str
    outcome: str
    is_override: bool
    tier: int | None = None
    approval_required: bool = False
    created_at: datetime


@router.get("/decision-log", response_model=list[DecisionLogOut])
async def list_decision_log(
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    agent_key: str | None = None,
    limit: int = 100,
):
    query = select(DecisionLog).where(DecisionLog.tenant_id == tenant_id)
    if agent_key:
        query = query.where(DecisionLog.agent_key == agent_key)
    result = await db.execute(query.order_by(DecisionLog.created_at.desc()).limit(limit))
    return result.scalars().all()


@router.get("/audit/tier-graduation")
async def tier_graduation_report(
    days: int = Query(default=90, ge=1, le=365),
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """AI-004 graduation evidence (tiering instructions §1.5, §5): per
    gated agent action over the trailing window, how often humans
    approved it versus overrode it. A tier-2 action with a high approval
    rate and a low override rate is a promotion candidate; tier 3 is
    reported but never promotable. Tier-1 volume is listed for
    after-the-fact visibility."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await db.execute(
        select(AIRecommendation.agent_key, AIRecommendation.action_type, AIRecommendation.tier, AIRecommendation.status, func.count())
        .where(AIRecommendation.tenant_id == tenant_id, AIRecommendation.created_at >= since)
        .group_by(AIRecommendation.agent_key, AIRecommendation.action_type, AIRecommendation.tier, AIRecommendation.status)
    )).all()
    gated: dict[tuple, dict] = {}
    for agent_key, action_type, tier, status, count in rows:
        entry = gated.setdefault((agent_key, action_type), {"agent_key": agent_key, "action_type": action_type, "tier": tier, "counts": {}})
        entry["counts"][status.value] = entry["counts"].get(status.value, 0) + count
    gated_actions = []
    for entry in gated.values():
        counts = entry["counts"]
        approved = counts.get(RecommendationStatus.APPROVED.value, 0)
        overridden = counts.get(RecommendationStatus.REJECTED.value, 0) + counts.get(RecommendationStatus.MODIFIED.value, 0)
        decided = approved + overridden
        gated_actions.append({
            **entry,
            "decided": decided,
            "approval_rate": round(approved / decided, 3) if decided else None,
            "override_rate": round(overridden / decided, 3) if decided else None,
            "promotable": entry["tier"] != 3,
        })

    tier_1 = (await db.execute(
        select(DecisionLog.agent_key, DecisionLog.trigger_event_type, func.count())
        .where(DecisionLog.tenant_id == tenant_id, DecisionLog.created_at >= since, DecisionLog.tier == 1)
        .group_by(DecisionLog.agent_key, DecisionLog.trigger_event_type)
    )).all()
    return {
        "window_days": days,
        "gated_actions": sorted(gated_actions, key=lambda e: (e["agent_key"], e["action_type"])),
        "tier_1_volume": [{"agent_key": a, "trigger_event_type": e, "decisions": c} for a, e, c in sorted(tier_1)],
    }

