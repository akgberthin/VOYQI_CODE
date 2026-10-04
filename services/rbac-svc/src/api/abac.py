import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import abac_engine, audit_engine

router = APIRouter(prefix="/api/v1/rbac/abac-policies", tags=["abac"])


class PolicyCreate(BaseModel):
    name: str
    source_role_key: str
    target_bundle_key: str


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    source_role_key: str
    target_bundle_key: str
    status: str
    conflicts_detected: list
    accountable_role: str | None
    applied_by: str | None


@router.post("", response_model=PolicyOut, status_code=201)
async def create_policy(body: PolicyCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await abac_engine.create_policy(db, tenant_id, body.name, body.source_role_key, body.target_bundle_key)


@router.get("", response_model=list[PolicyOut])
async def list_policies(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await abac_engine.list_policies(db, tenant_id)


@router.get("/{policy_id}", response_model=PolicyOut)
async def get_policy(policy_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    policy = await abac_engine.get_policy(db, tenant_id, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@router.post("/{policy_id}/simulate", response_model=PolicyOut)
async def simulate(policy_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous — re-runs on every call, no approval needed to
    preview. Never changes what any user can actually do."""
    try:
        return await abac_engine.simulate(db, tenant_id, policy_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ApplyRequest(BaseModel):
    accountable_role: str
    applied_by: str


@router.post("/{policy_id}/apply", response_model=PolicyOut)
async def apply_policy(policy_id: uuid.UUID, body: ApplyRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The hard Tier-3 gate — structurally refuses without a real
    accountable_role and without a prior simulation. No threshold, no
    bypass, no AI code path anywhere in this call."""
    try:
        policy = await abac_engine.apply_policy(db, tenant_id, policy_id, body.accountable_role, body.applied_by)
    except abac_engine.AccountableRoleRequiredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except abac_engine.PolicyNotSimulatedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit_engine.record(db, tenant_id, "human", body.applied_by, "abac_policy.applied", str(policy_id), f"accountable_role={body.accountable_role}")
    return policy


@router.post("/{policy_id}/reject", response_model=PolicyOut)
async def reject_policy(policy_id: uuid.UUID, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        policy = await abac_engine.reject_policy(db, tenant_id, policy_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit_engine.record(db, tenant_id, "human", actor_id, "abac_policy.rejected", str(policy_id))
    return policy
