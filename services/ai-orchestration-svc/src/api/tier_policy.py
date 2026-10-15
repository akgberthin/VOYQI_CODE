"""AI-005 tier policy management: the effective policy for a tenant and
the per-action changes it allows (rules in core/tier_overrides.py)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import tier_overrides, tiering
from src.core.database import get_db
from src.core.security import get_current_actor, get_tenant_id

router = APIRouter(prefix="/api/v1/ai/tier-policy", tags=["tier-policy"])


class TierChange(BaseModel):
    tier: int
    threshold: str | None = None
    reason: str


@router.get("")
async def effective_policy(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    overrides = {(o.policy_agent, o.action_type): o for o in await tier_overrides.list_overrides(db, tenant_id)}
    policy = tiering._load_policy()
    actions = []
    for agent, cfg in sorted(policy["agents"].items()):
        for action, base in sorted(cfg["actions"].items()):
            override = overrides.get((agent, action))
            actions.append({
                "policy_agent": agent, "action_type": action,
                "base_tier": base["tier"], "base_threshold": base.get("threshold"),
                "tier": override.tier if override else base["tier"],
                "threshold": override.threshold if override else base.get("threshold"),
                "changeable": base["tier"] != 3,
                "override": None if override is None else {
                    "reason": override.reason, "evidence": override.evidence, "changed_by": override.changed_by, "changed_at": override.changed_at,
                },
            })
    return {"default_tier": policy["default_tier"], "actions": actions}


@router.get("/{policy_agent}/{action_type}/evidence")
async def evidence(policy_agent: str, action_type: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await tier_overrides.promotion_evidence(db, tenant_id, policy_agent, action_type)


@router.put("/{policy_agent}/{action_type}")
async def change_tier(
    policy_agent: str, action_type: str, body: TierChange,
    tenant_id: str = Depends(get_tenant_id), actor: str = Depends(get_current_actor), db: AsyncSession = Depends(get_db),
):
    try:
        override = await tier_overrides.set_override(db, tenant_id, policy_agent, action_type, body.tier, body.threshold, body.reason, actor)
    except tier_overrides.TierChangeRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"policy_agent": policy_agent, "action_type": action_type, "tier": override.tier, "threshold": override.threshold, "evidence": override.evidence}


@router.delete("/{policy_agent}/{action_type}", status_code=204)
async def revert(policy_agent: str, action_type: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Back to the policy file's tier for this action."""
    await tier_overrides.clear_override(db, tenant_id, policy_agent, action_type)
