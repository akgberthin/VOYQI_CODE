from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import member_experience_agent

router = APIRouter(prefix="/api/v1/ai/member-experience", tags=["member-experience-agent"])


class ChurnRiskRequest(BaseModel):
    member_profile_id: str
    context: dict[str, Any] = {}


class WinbackRequest(BaseModel):
    member_profile_id: str
    subscription_id: str
    reason: str


@router.post("/evaluate-churn-risk")
async def evaluate_churn_risk(body: ChurnRiskRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await member_experience_agent.evaluate_churn_risk(db, tenant_id, body.member_profile_id, body.context)
    return {
        "risk_score": result.risk_score,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


@router.post("/evaluate-winback")
async def evaluate_winback(body: WinbackRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """This is the exact endpoint subscription-svc's initiate_cancellation
    calls — see its README for why that call is designed to never block
    a member's cancellation even if this endpoint is slow or errors."""
    result = await member_experience_agent.evaluate_winback(
        db, tenant_id, body.member_profile_id, body.subscription_id, body.reason
    )
    return {
        "offer": result.offer,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class UpgradePatternRequest(BaseModel):
    member_profile_id: str
    subscription_id: str
    current_class: str


@router.post("/evaluate-upgrade-pattern")
async def evaluate_upgrade_pattern(body: UpgradePatternRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """This is the endpoint subscription-svc's record_above_class_request
    calls after logging each signal — fully autonomous through case
    creation, since offering an upgrade case never touches billing."""
    result = await member_experience_agent.evaluate_upgrade_pattern(
        db, tenant_id, body.member_profile_id, body.subscription_id, body.current_class
    )
    return {
        "case_created": result.case_created,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }
