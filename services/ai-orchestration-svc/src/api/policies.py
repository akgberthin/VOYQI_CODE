import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_current_actor, get_tenant_id
from src.models import PolicyStatus, PromptPolicy

router = APIRouter(prefix="/api/v1/ai", tags=["policies"])


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_key: str
    version: int
    system_prompt: str
    status: PolicyStatus
    eval_suite_passed: bool
    created_by: str
    approved_by: str | None


class PolicyCreate(BaseModel):
    agent_key: str
    system_prompt: str


class PolicyPromote(BaseModel):
    eval_suite_passed: bool
    eval_summary: str = ""


@router.post("/policies", response_model=PolicyOut, status_code=201)
async def create_policy_draft(
    body: PolicyCreate,
    tenant_id: str = Depends(get_tenant_id),
    actor: str = Depends(get_current_actor),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PromptPolicy)
        .where(PromptPolicy.tenant_id == tenant_id, PromptPolicy.agent_key == body.agent_key)
        .order_by(PromptPolicy.version.desc())
    )
    latest = result.scalars().first()
    next_version = (latest.version + 1) if latest else 1

    policy = PromptPolicy(
        tenant_id=tenant_id,
        agent_key=body.agent_key,
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


@router.post("/policies/{policy_id}/promote", response_model=PolicyOut)
async def promote_policy(
    policy_id: uuid.UUID,
    body: PolicyPromote,
    tenant_id: str = Depends(get_tenant_id),
    actor: str = Depends(get_current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Promotion requires evaluation-suite results before it can go active
    — a policy cannot be promoted based on vibes, only a passed eval run.
    Promoting retires whichever version was previously active; nothing is
    deleted, so rollback is just promoting an older version again."""
    result = await db.execute(
        select(PromptPolicy).where(PromptPolicy.id == policy_id, PromptPolicy.tenant_id == tenant_id)
    )
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    if not body.eval_suite_passed:
        raise HTTPException(status_code=422, detail="Cannot promote a policy without a passed eval suite")

    prior_active = await db.execute(
        select(PromptPolicy).where(
            PromptPolicy.tenant_id == tenant_id,
            PromptPolicy.agent_key == policy.agent_key,
            PromptPolicy.status == PolicyStatus.ACTIVE,
        )
    )
    for prior in prior_active.scalars().all():
        prior.status = PolicyStatus.RETIRED

    policy.status = PolicyStatus.ACTIVE
    policy.eval_suite_passed = True
    policy.eval_summary = body.eval_summary
    policy.approved_by = actor
    policy.promoted_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(policy)
    return policy
