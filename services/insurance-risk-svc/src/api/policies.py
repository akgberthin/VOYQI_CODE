import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import PolicyStatus
from src.services import policy_engine

router = APIRouter(prefix="/api/v1/insurance-risk/policies", tags=["policies"])


class PolicyIn(BaseModel):
    policy_number: str
    policy_type: str
    insured_name: str
    coverage_amount_usd: float


class PolicyOut(BaseModel):
    id: str
    policy_number: str
    policy_type: str
    insured_name: str
    coverage_amount_usd: float
    status: PolicyStatus


class StatusIn(BaseModel):
    status: PolicyStatus


def _to_out(p) -> PolicyOut:
    return PolicyOut(
        id=str(p.id), policy_number=p.policy_number, policy_type=p.policy_type,
        insured_name=p.insured_name, coverage_amount_usd=p.coverage_amount_usd, status=p.status,
    )


@router.post("", response_model=PolicyOut, status_code=201)
async def create_policy(
    payload: PolicyIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> PolicyOut:
    policy = await policy_engine.create_policy(
        db, tenant_id, payload.policy_number, payload.policy_type, payload.insured_name, payload.coverage_amount_usd
    )
    return _to_out(policy)


@router.get("", response_model=list[PolicyOut])
async def list_policies(
    status: PolicyStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> list[PolicyOut]:
    policies = await policy_engine.list_policies(db, tenant_id, status)
    return [_to_out(p) for p in policies]


@router.get("/{policy_id}", response_model=PolicyOut)
async def get_policy(
    policy_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> PolicyOut:
    policy = await policy_engine.get_policy(db, tenant_id, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _to_out(policy)


@router.post("/{policy_id}/status", response_model=PolicyOut)
async def set_policy_status(
    policy_id: uuid.UUID, payload: StatusIn,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
) -> PolicyOut:
    policy = await policy_engine.set_policy_status(db, tenant_id, policy_id, payload.status)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _to_out(policy)
