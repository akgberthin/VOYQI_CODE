from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import revenue_agent

router = APIRouter(prefix="/api/v1/ai/crm", tags=["revenue-agent"])
org_router = APIRouter(prefix="/api/v1/ai/org", tags=["revenue-agent"])


class IdentityResolutionRequest(BaseModel):
    source_record_id: str
    full_name: str
    email: str = ""
    phone: str = ""


@router.post("/evaluate-identity-resolution")
async def evaluate_identity_resolution(
    body: IdentityResolutionRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await revenue_agent.evaluate_identity_resolution(
        db, tenant_id=tenant_id, source_record_id=body.source_record_id, full_name=body.full_name, email=body.email, phone=body.phone,
    )
    return {
        "candidate_person_profile_id": result.candidate_person_profile_id,
        "match_score": result.match_score,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class LeadScoringRequest(BaseModel):
    lead_id: str
    source: str
    full_name: str
    email: str = ""
    phone: str = ""


@router.post("/evaluate-lead-scoring")
async def evaluate_lead_scoring(body: LeadScoringRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await revenue_agent.evaluate_lead_scoring(
        db, tenant_id=tenant_id, lead_id=body.lead_id, source=body.source, full_name=body.full_name, email=body.email, phone=body.phone,
    )
    return {
        "lead_id": result.lead_id,
        "score": result.score,
        "routed_to_team": result.routed_to_team,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class ProfileDeduplicationRequest(BaseModel):
    person_profile_id: str
    full_name: str
    email: str = ""
    phone: str = ""


@router.post("/evaluate-profile-deduplication")
async def evaluate_profile_deduplication(body: ProfileDeduplicationRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await revenue_agent.evaluate_profile_deduplication(
        db, tenant_id=tenant_id, person_profile_id=body.person_profile_id, full_name=body.full_name, email=body.email, phone=body.phone,
    )
    return {
        "candidate_profile_id": result.candidate_profile_id,
        "match_score": result.match_score,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class PrequalificationRequest(BaseModel):
    case_id: str
    origination_type: str
    full_name: str
    external_ref_id: str = ""


@org_router.post("/evaluate-prequalification")
async def evaluate_prequalification(body: PrequalificationRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await revenue_agent.evaluate_origination_prequalification(
        db, tenant_id=tenant_id, case_id=body.case_id, origination_type=body.origination_type, full_name=body.full_name, external_ref_id=body.external_ref_id,
    )
    return {
        "verdict": result.verdict,
        "next_step_guidance": result.next_step_guidance,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class RiskScoringRequest(BaseModel):
    decision_id: str
    case_id: str
    origination_type: str
    full_name: str
    external_ref_id: str = ""


@org_router.post("/evaluate-risk-scoring")
async def evaluate_risk_scoring(body: RiskScoringRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await revenue_agent.evaluate_origination_decisioning(
        db, tenant_id=tenant_id, decision_id=body.decision_id, case_id=body.case_id, origination_type=body.origination_type,
        full_name=body.full_name, external_ref_id=body.external_ref_id,
    )
    return {
        "decision_id": result.decision_id,
        "risk_score": result.risk_score,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class FundingMatchRequest(BaseModel):
    case_id: str
    origination_type: str
    risk_score: float
    amount_usd: float


@org_router.post("/evaluate-funding-match")
async def evaluate_funding_match(body: FundingMatchRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await revenue_agent.evaluate_funding_source_matching(
        db, tenant_id=tenant_id, case_id=body.case_id, origination_type=body.origination_type, risk_score=body.risk_score, amount_usd=body.amount_usd,
    )
    return {
        "candidate_funding_source_id": result.candidate_funding_source_id,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }
