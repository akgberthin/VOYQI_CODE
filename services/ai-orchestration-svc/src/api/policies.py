import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import prompts
from src.core.database import get_db
from src.core.security import get_current_actor, get_tenant_id
from src.models import PolicyStatus, PromptPolicy

router = APIRouter(prefix="/api/v1/ai", tags=["policies"])


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_key: str
    prompt_key: str | None
    version: int
    system_prompt: str
    status: PolicyStatus
    eval_suite_passed: bool
    eval_summary: str
    created_by: str
    approved_by: str | None


class PolicyCreate(BaseModel):
    system_prompt: str
    # "<agent module>.<CONSTANT>" from GET /prompts. Without it the version
    # is agent-level only and never takes effect.
    prompt_key: str | None = None
    agent_key: str | None = None


@router.get("/prompts")
async def list_prompts(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Every agent prompt that can be versioned, what a new version must
    keep, and which version is live for this tenant (the code's default
    when none has been promoted)."""
    result = await db.execute(
        select(PromptPolicy.prompt_key, PromptPolicy.version)
        .where(PromptPolicy.tenant_id == tenant_id, PromptPolicy.status == PolicyStatus.ACTIVE, PromptPolicy.prompt_key.is_not(None))
    )
    active = dict(result.all())
    return [
        {
            "prompt_key": key, "agent_key": key.split(".", 1)[0], "default_prompt": text,
            "required": prompts.required_markers(key), "active_version": active.get(key),
        }
        for key, text in sorted(prompts.registry().items())
    ]


@router.post("/policies", response_model=PolicyOut, status_code=201)
async def create_policy_draft(
    body: PolicyCreate,
    tenant_id: str = Depends(get_tenant_id),
    actor: str = Depends(get_current_actor),
    db: AsyncSession = Depends(get_db),
):
    if body.prompt_key is not None and body.prompt_key not in prompts.registry():
        raise HTTPException(status_code=422, detail=f"Unknown prompt_key {body.prompt_key!r} — see GET /api/v1/ai/prompts")
    agent_key = body.prompt_key.split(".", 1)[0] if body.prompt_key else body.agent_key
    if not agent_key:
        raise HTTPException(status_code=422, detail="prompt_key (or, for a legacy agent-level draft, agent_key) is required")
    series = PromptPolicy.prompt_key == body.prompt_key if body.prompt_key else (PromptPolicy.agent_key == agent_key) & PromptPolicy.prompt_key.is_(None)
    result = await db.execute(
        select(PromptPolicy).where(PromptPolicy.tenant_id == tenant_id, series).order_by(PromptPolicy.version.desc())
    )
    latest = result.scalars().first()
    next_version = (latest.version + 1) if latest else 1

    policy = PromptPolicy(
        tenant_id=tenant_id,
        agent_key=agent_key,
        prompt_key=body.prompt_key,
        version=next_version,
        system_prompt=body.system_prompt,
        created_by=actor,
        status=PolicyStatus.DRAFT,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.get("/policies", response_model=list[PolicyOut])
async def list_policies(
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db), agent_key: str | None = None
):
    query = select(PromptPolicy).where(PromptPolicy.tenant_id == tenant_id)
    if agent_key:
        query = query.where(PromptPolicy.agent_key == agent_key)
    result = await db.execute(query.order_by(PromptPolicy.version.desc()))
    return result.scalars().all()


async def _get_policy(db: AsyncSession, tenant_id: str, policy_id: uuid.UUID) -> PromptPolicy:
    result = await db.execute(select(PromptPolicy).where(PromptPolicy.id == policy_id, PromptPolicy.tenant_id == tenant_id))
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@router.post("/policies/{policy_id}/evaluate", response_model=PolicyOut)
async def evaluate_policy(policy_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Runs the evaluation suite server-side and records the result — the
    only way a version becomes promotable."""
    policy = await _get_policy(db, tenant_id, policy_id)
    if policy.prompt_key is None:
        raise HTTPException(status_code=422, detail="Only prompt-level versions (with a prompt_key) can be evaluated")
    passed, findings = prompts.evaluate(policy.prompt_key, policy.system_prompt)
    policy.eval_suite_passed = passed
    policy.eval_summary = "all checks passed" if passed else "; ".join(findings)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.post("/policies/{policy_id}/promote", response_model=PolicyOut)
async def promote_policy(
    policy_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    actor: str = Depends(get_current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Promotion requires this version to have passed the server-side
    evaluation (POST .../evaluate) — the caller can't simply assert it.
    Promoting retires whichever version was previously active and takes
    effect for this tenant's agents immediately (other replicas within a
    minute); nothing is deleted, so rollback is promoting an older version."""
    policy = await _get_policy(db, tenant_id, policy_id)
    if not policy.eval_suite_passed:
        raise HTTPException(status_code=422, detail="Cannot promote a version that hasn't passed the evaluation suite — run POST .../evaluate")

    prior_active = await db.execute(
        select(PromptPolicy).where(
            PromptPolicy.tenant_id == tenant_id,
            PromptPolicy.agent_key == policy.agent_key,
            PromptPolicy.prompt_key.is_(policy.prompt_key) if policy.prompt_key is None else PromptPolicy.prompt_key == policy.prompt_key,
            PromptPolicy.status == PolicyStatus.ACTIVE,
        )
    )
    for prior in prior_active.scalars().all():
        prior.status = PolicyStatus.RETIRED

    policy.status = PolicyStatus.ACTIVE
    policy.approved_by = actor
    policy.promoted_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(policy)
    if policy.prompt_key:
        prompts.set_active(tenant_id, policy.prompt_key, policy.system_prompt)
    return policy
