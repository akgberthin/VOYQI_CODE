import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import CreditDecisionStatus
from src.services import credit_decision_engine
from src.services.credit_decision_engine import (
    AccountableRoleRequiredError,
    AlreadyDecidedError,
    ApplicationNotFoundError,
)

router = APIRouter(prefix="/api/v1/originations/credit-decisions", tags=["credit-decisions"])


class RequestIn(BaseModel):
    application_id: uuid.UUID


class DecideIn(BaseModel):
    approve: bool
    accountable_role: str | None = None
    reasoning: str = ""


class DecisionOut(BaseModel):
    id: str
    application_id: str
    status: CreditDecisionStatus
    accountable_role: str | None
    decided_by: str | None
    decision_reasoning: str | None
    decided_at: datetime | None


def _to_out(d) -> DecisionOut:
    return DecisionOut(
        id=str(d.id), application_id=str(d.application_id), status=d.status,
        accountable_role=d.accountable_role, decided_by=d.decided_by,
        decision_reasoning=d.decision_reasoning, decided_at=d.decided_at,
    )


@router.post("", response_model=DecisionOut, status_code=201)
async def request_decision(
    payload: RequestIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> DecisionOut:
    try:
        decision = await credit_decision_engine.request_decision(db, tenant_id, payload.application_id)
    except ApplicationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_out(decision)


@router.get("", response_model=list[DecisionOut])
async def list_decisions(
    status: CreditDecisionStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> list[DecisionOut]:
    decisions = await credit_decision_engine.list_decisions(db, tenant_id, status)
    return [_to_out(d) for d in decisions]


@router.post("/{decision_id}/decide", response_model=DecisionOut)
async def decide(
    decision_id: uuid.UUID, payload: DecideIn,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
) -> DecisionOut:
    try:
        decision = await credit_decision_engine.decide(
            db, tenant_id, decision_id, payload.approve, payload.accountable_role, "api-caller", payload.reasoning
        )
    except AccountableRoleRequiredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if decision is None:
        raise HTTPException(status_code=404, detail="Credit decision not found")
    return _to_out(decision)
