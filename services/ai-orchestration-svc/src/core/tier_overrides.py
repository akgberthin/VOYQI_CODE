"""AI-005 — per-tenant changes to tier-policy.yaml, with the rules from
docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md enforced in code:

- A tier-3 action can never be changed (§1.4): fair lending, employment
  law and access policy are a legal conversation, not a setting.
- Nothing can be set to tier 3 here either — that category is defined in
  the policy file, reviewed like any production change.
- Moving an action to tier 1 (removing its approval gate) needs evidence
  (§5): over the trailing 90 days at least MIN_DECISIONS human decisions
  on it, an approval rate of at least MIN_APPROVAL_RATE and an override
  rate of at most MAX_OVERRIDE_RATE. Making an action stricter (tier 2)
  needs none.
- Only actions that exist in the policy file can be changed.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import tiering
from src.models import AIRecommendation, RecommendationStatus, TierOverride

EVIDENCE_WINDOW_DAYS = 90
MIN_DECISIONS = 20
MIN_APPROVAL_RATE = 0.9
MAX_OVERRIDE_RATE = 0.1


class TierChangeRefused(Exception):
    pass


async def promotion_evidence(db: AsyncSession, tenant_id: str, policy_agent: str, action_type: str) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=EVIDENCE_WINDOW_DAYS)
    result = await db.execute(
        select(AIRecommendation).where(
            AIRecommendation.tenant_id == tenant_id, AIRecommendation.action_type == action_type, AIRecommendation.created_at >= since,
        )
    )
    policy = f"{policy_agent}.{action_type}"
    decided = approved = 0
    for rec in result.scalars():
        if (rec.proposed_action or {}).get("policy", policy) != policy:
            continue
        if rec.status == RecommendationStatus.APPROVED:
            decided += 1
            approved += 1
        elif rec.status in (RecommendationStatus.REJECTED, RecommendationStatus.MODIFIED):
            decided += 1
    approval = approved / decided if decided else 0.0
    return {
        "window_days": EVIDENCE_WINDOW_DAYS, "decided": decided,
        "approval_rate": round(approval, 3), "override_rate": round(1 - approval, 3) if decided else None,
        "sufficient": decided >= MIN_DECISIONS and approval >= MIN_APPROVAL_RATE and (1 - approval) <= MAX_OVERRIDE_RATE,
    }


async def set_override(
    db: AsyncSession, tenant_id: str, policy_agent: str, action_type: str, tier: int, threshold: str | None, reason: str, actor: str,
) -> TierOverride:
    base = tiering.base_action(policy_agent, action_type)
    if base is None:
        raise TierChangeRefused(f"{policy_agent}.{action_type} is not in the tier policy — add it there first")
    if base["tier"] == 3:
        raise TierChangeRefused(f"{policy_agent}.{action_type} is tier 3; tier-3 actions cannot be changed here")
    if tier not in (1, 2):
        raise TierChangeRefused("only tier 1 or tier 2 can be set here; tier 3 is defined in the policy file")
    if not reason.strip():
        raise TierChangeRefused("a reason is required")
    evidence = ""
    if tier == 1 and base["tier"] != 1:
        found = await promotion_evidence(db, tenant_id, policy_agent, action_type)
        if not found["sufficient"]:
            raise TierChangeRefused(
                f"not enough evidence to remove the approval gate: {found['decided']} decisions in {EVIDENCE_WINDOW_DAYS} days "
                f"(need {MIN_DECISIONS}), approval rate {found['approval_rate']:.0%} (need {MIN_APPROVAL_RATE:.0%})"
            )
        evidence = f"{found['decided']} decisions, {found['approval_rate']:.0%} approved over {EVIDENCE_WINDOW_DAYS} days"

    result = await db.execute(
        select(TierOverride).where(
            TierOverride.tenant_id == tenant_id, TierOverride.policy_agent == policy_agent, TierOverride.action_type == action_type,
        )
    )
    override = result.scalar_one_or_none() or TierOverride(tenant_id=tenant_id, policy_agent=policy_agent, action_type=action_type)
    override.tier, override.threshold, override.reason, override.evidence, override.changed_by = tier, threshold, reason.strip(), evidence, actor
    override.changed_at = datetime.now(timezone.utc)
    db.add(override)
    await db.commit()
    await db.refresh(override)
    tiering._overrides[(tenant_id, policy_agent, action_type)] = (tier, threshold)
    return override


async def clear_override(db: AsyncSession, tenant_id: str, policy_agent: str, action_type: str) -> bool:
    result = await db.execute(
        select(TierOverride).where(
            TierOverride.tenant_id == tenant_id, TierOverride.policy_agent == policy_agent, TierOverride.action_type == action_type,
        )
    )
    override = result.scalar_one_or_none()
    tiering._overrides.pop((tenant_id, policy_agent, action_type), None)
    if override is None:
        return False
    await db.delete(override)
    await db.commit()
    return True


async def refresh(db: AsyncSession) -> int:
    rows = (await db.execute(select(TierOverride))).scalars().all()
    fresh = {(o.tenant_id, o.policy_agent, o.action_type): (o.tier, o.threshold) for o in rows}
    tiering._overrides.clear()
    tiering._overrides.update(fresh)
    return len(fresh)


async def list_overrides(db: AsyncSession, tenant_id: str) -> list[TierOverride]:
    return list((await db.execute(select(TierOverride).where(TierOverride.tenant_id == tenant_id))).scalars())
