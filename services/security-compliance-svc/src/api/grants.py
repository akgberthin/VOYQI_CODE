import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import GrantRequestStatus, SubjectType
from src.services import grant_engine
from src.services.grant_engine import (
    AccountableRoleRequiredError,
    AlreadyDecidedError,
    NotSimulatedError,
    PolicyNotFoundError,
)

router = APIRouter(prefix="/api/v1/security-compliance/grants", tags=["grants"])


class GrantRequestIn(BaseModel):
    policy_id: uuid.UUID
    subject_type: SubjectType
    subject_id: str


class ApplyIn(BaseModel):
    accountable_role: str | None = None


class GrantRequestOut(BaseModel):
    id: str
    policy_id: str
    subject_type: SubjectType
    subject_id: str
    simulated: bool
    simulation_result: dict | None
    status: GrantRequestStatus
    accountable_role: str | None
    decided_by: str | None


def _to_out(r) -> GrantRequestOut:
    return GrantRequestOut(
        id=str(r.id), policy_id=str(r.policy_id), subject_type=r.subject_type, subject_id=r.subject_id,
        simulated=r.simulated, simulation_result=r.simulation_result, status=r.status,
        accountable_role=r.accountable_role, decided_by=r.decided_by,
    )


@router.post("", response_model=GrantRequestOut, status_code=201)
async def request_grant(
    payload: GrantRequestIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> GrantRequestOut:
    try:
        request = await grant_engine.request_grant(db, tenant_id, payload.policy_id, payload.subject_type, payload.subject_id)
    except PolicyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_out(request)


@router.get("", response_model=list[GrantRequestOut])
async def list_grant_requests(
    status: GrantRequestStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> list[GrantRequestOut]:
    requests = await grant_engine.list_grant_requests(db, tenant_id, status)
    return [_to_out(r) for r in requests]


@router.post("/{request_id}/simulate", response_model=GrantRequestOut)
async def simulate_grant(
    request_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> GrantRequestOut:
    request = await grant_engine.simulate_grant(db, tenant_id, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Grant request not found")
    return _to_out(request)


@router.post("/{request_id}/apply", response_model=GrantRequestOut)
async def apply_grant(
    request_id: uuid.UUID, payload: ApplyIn,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
) -> GrantRequestOut:
    try:
        request = await grant_engine.apply_grant(db, tenant_id, request_id, "api-caller", payload.accountable_role)
    except NotSimulatedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AccountableRoleRequiredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if request is None:
        raise HTTPException(status_code=404, detail="Grant request not found")
    return _to_out(request)


@router.post("/{request_id}/reject", response_model=GrantRequestOut)
async def reject_grant(
    request_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> GrantRequestOut:
    try:
        request = await grant_engine.reject_grant(db, tenant_id, request_id, "api-caller")
    except AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if request is None:
        raise HTTPException(status_code=404, detail="Grant request not found")
    return _to_out(request)
