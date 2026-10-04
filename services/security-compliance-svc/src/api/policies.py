from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import PolicyStatus
from src.services import policy_engine

router = APIRouter(prefix="/api/v1/security-compliance/policies", tags=["policies"])


class PolicyIn(BaseModel):
    key: str
    name: str
    description: str


class PolicyOut(BaseModel):
    id: str
    key: str
    name: str
    description: str
    status: PolicyStatus


def _to_out(p) -> PolicyOut:
    return PolicyOut(id=str(p.id), key=p.key, name=p.name, description=p.description, status=p.status)


@router.post("", response_model=PolicyOut, status_code=201)
async def create_policy(
    payload: PolicyIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> PolicyOut:
    policy = await policy_engine.create_policy(db, tenant_id, payload.key, payload.name, payload.description)
    return _to_out(policy)


@router.get("", response_model=list[PolicyOut])
async def list_policies(
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> list[PolicyOut]:
    policies = await policy_engine.list_policies(db, tenant_id)
    return [_to_out(p) for p in policies]
