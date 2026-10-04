import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import RiskBand
from src.services import risk_engine

router = APIRouter(prefix="/api/v1/insurance-risk/policies/{policy_id}/assessments", tags=["assessments"])


class AssessmentIn(BaseModel):
    claims_last_12mo: int = 0


class AssessmentOut(BaseModel):
    id: str
    policy_id: str
    risk_score: float
    risk_band: RiskBand
    drivers: list[str]
    reasoning: str
    created_at: datetime


def _to_out(a) -> AssessmentOut:
    return AssessmentOut(
        id=str(a.id), policy_id=str(a.policy_id), risk_score=a.risk_score,
        risk_band=a.risk_band, drivers=a.drivers, reasoning=a.reasoning, created_at=a.created_at,
    )


@router.post("", response_model=AssessmentOut, status_code=201)
async def run_assessment(
    policy_id: uuid.UUID, payload: AssessmentIn,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
) -> AssessmentOut:
    assessment = await risk_engine.run_assessment(db, tenant_id, policy_id, payload.claims_last_12mo)
    if assessment is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _to_out(assessment)


@router.get("", response_model=list[AssessmentOut])
async def list_assessments(
    policy_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> list[AssessmentOut]:
    assessments = await risk_engine.list_assessments(db, tenant_id, policy_id)
    return [_to_out(a) for a in assessments]
