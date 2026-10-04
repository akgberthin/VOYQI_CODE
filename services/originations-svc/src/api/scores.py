import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import LeadBand
from src.services import scoring_engine

router = APIRouter(prefix="/api/v1/originations/applications/{application_id}/scores", tags=["scores"])


class ScoreOut(BaseModel):
    id: str
    application_id: str
    score: float
    band: LeadBand
    drivers: list[str]
    reasoning: str
    created_at: datetime


def _to_out(s) -> ScoreOut:
    return ScoreOut(
        id=str(s.id), application_id=str(s.application_id), score=s.score, band=s.band,
        drivers=s.drivers, reasoning=s.reasoning, created_at=s.created_at,
    )


@router.post("", response_model=ScoreOut, status_code=201)
async def score_application(
    application_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> ScoreOut:
    score = await scoring_engine.score_application(db, tenant_id, application_id)
    if score is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return _to_out(score)


@router.get("", response_model=list[ScoreOut])
async def list_scores(
    application_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> list[ScoreOut]:
    scores = await scoring_engine.list_scores(db, tenant_id, application_id)
    return [_to_out(s) for s in scores]
