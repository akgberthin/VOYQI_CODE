"""ORG-001..003 under /api/v1/org — the paths ai-orchestration-svc's
org_tools already calls (prequalification, apply-score, funding
sources, disparate-impact snapshot)."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import OrgDecisionStatus, OriginationCaseStatus, OriginationType, RecommendedOutcome
from src.services import org_engine

router = APIRouter(prefix="/api/v1/org", tags=["org"])


class CaseIn(BaseModel):
    origination_type: OriginationType
    full_name: str
    amount_usd: float = Field(gt=0)
    email: str | None = None
    phone: str | None = None
    external_ref_id: str | None = None


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    origination_type: OriginationType
    full_name: str
    email: str | None
    phone: str | None
    external_ref_id: str | None
    amount_usd: float
    status: OriginationCaseStatus
    prequal_verdict: str | None
    prequal_reasoning: str | None
    prequal_next_step: str | None
    created_at: datetime | None


class PrequalificationIn(BaseModel):
    verdict: str
    reasoning: str
    next_step_guidance: str = ""


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    case_id: uuid.UUID
    status: OrgDecisionStatus
    risk_score: float | None
    score_reasoning: str | None
    recommended_outcome: RecommendedOutcome | None
    accountable_role: str | None
    decided_by: str | None
    decision_reasoning: str | None
    decided_at: datetime | None


class ApplyScoreIn(BaseModel):
    risk_score: float
    reasoning: str


class FinalizeIn(BaseModel):
    approve: bool
    accountable_role: str | None = None
    decided_by: str = "underwriter"
    reasoning: str = ""


class FundingSourceIn(BaseModel):
    name: str
    applicable_origination_type: OriginationType
    min_risk_score: float = Field(ge=0.0, le=1.0)
    max_amount_usd: float = Field(gt=0)
    reserve_participation_pct: float = Field(default=0.0, ge=0.0, le=100.0)


class FundingSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    applicable_origination_type: OriginationType
    min_risk_score: float
    max_amount_usd: float
    reserve_participation_pct: float
    active: bool


@router.post("/cases", response_model=CaseOut, status_code=201)
async def create_case(body: CaseIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await org_engine.create_case(
        db, tenant_id, body.origination_type, body.full_name, body.amount_usd, body.email, body.phone, body.external_ref_id
    )


@router.get("/cases", response_model=list[CaseOut])
async def list_cases(status: OriginationCaseStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await org_engine.list_cases(db, tenant_id, status)


@router.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(case_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    case = await org_engine.get_case(db, tenant_id, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.post("/cases/{case_id}/prequalification", response_model=CaseOut)
async def set_prequalification(case_id: uuid.UUID, body: PrequalificationIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await org_engine.set_prequalification(db, tenant_id, case_id, body.verdict, body.reasoning, body.next_step_guidance)
    except org_engine.CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/cases/{case_id}/decisions", response_model=DecisionOut, status_code=201)
async def request_decisioning(case_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await org_engine.request_decisioning(db, tenant_id, case_id)
    except org_engine.CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except org_engine.OpenDecisionExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/decisions", response_model=list[DecisionOut])
async def list_decisions(status: OrgDecisionStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await org_engine.list_decisions(db, tenant_id, status)


@router.get("/decisions/{decision_id}", response_model=DecisionOut)
async def get_decision(decision_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    decision = await org_engine.get_decision(db, tenant_id, decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return decision


@router.post("/decisions/{decision_id}/apply-score", response_model=DecisionOut)
async def apply_score(decision_id: uuid.UUID, body: ApplyScoreIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await org_engine.apply_score(db, tenant_id, decision_id, body.risk_score, body.reasoning)
    except org_engine.DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except org_engine.InvalidRiskScoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except org_engine.AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/decisions/{decision_id}/finalize", response_model=DecisionOut)
async def finalize_decision(decision_id: uuid.UUID, body: FinalizeIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Tier-3 credit.decision gate: refuses without accountable_role,
    for a decline exactly as much as an approval."""
    try:
        return await org_engine.finalize_decision(db, tenant_id, decision_id, body.approve, body.accountable_role, body.decided_by, body.reasoning)
    except org_engine.DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (org_engine.AccountableRoleRequiredError, org_engine.NotScoredError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except org_engine.AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/compliance/disparate-impact-snapshot")
async def disparate_impact_snapshot(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await org_engine.disparate_impact_snapshot(db, tenant_id)


@router.post("/funding-sources", response_model=FundingSourceOut, status_code=201)
async def create_funding_source(body: FundingSourceIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await org_engine.create_funding_source(
        db, tenant_id, body.name, body.applicable_origination_type, body.min_risk_score, body.max_amount_usd, body.reserve_participation_pct
    )


@router.get("/funding-sources", response_model=list[FundingSourceOut])
async def list_funding_sources(origination_type: OriginationType | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await org_engine.list_funding_sources(db, tenant_id, origination_type)
