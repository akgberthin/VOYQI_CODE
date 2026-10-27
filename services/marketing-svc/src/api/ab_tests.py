import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import ab_test_engine

router = APIRouter(prefix="/api/v1/marketing/ab-tests", tags=["ab-tests"])


class ABTestCreate(BaseModel):
    campaign_id: uuid.UUID
    variant_a_draft_id: uuid.UUID
    variant_b_draft_id: uuid.UUID
    metric: str = "click_rate"


class ABTestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    campaign_id: uuid.UUID
    variant_a_draft_id: uuid.UUID
    variant_b_draft_id: uuid.UUID
    metric: str
    variant_a_sent: int
    variant_a_converted: int
    variant_b_sent: int
    variant_b_converted: int
    status: str
    winner_draft_id: uuid.UUID | None
    concluded_at: datetime | None


@router.get("", response_model=list[ABTestOut])
async def list_ab_tests(campaign_id: uuid.UUID | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ab_test_engine.list_ab_tests(db, tenant_id, campaign_id)


@router.post("", response_model=ABTestOut, status_code=201)
async def create_ab_test(body: ABTestCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ab_test_engine.create_ab_test(db, tenant_id, body.campaign_id, body.variant_a_draft_id, body.variant_b_draft_id, body.metric)


@router.get("/{test_id}", response_model=ABTestOut)
async def get_ab_test(test_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    test = await ab_test_engine.get_ab_test(db, tenant_id, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="A/B test not found")
    return test


class ResultRecord(BaseModel):
    variant: str
    sent_delta: int
    converted_delta: int


@router.post("/{test_id}/record-result", response_model=ABTestOut)
async def record_result(test_id: uuid.UUID, body: ResultRecord, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    test = await ab_test_engine.record_result(db, tenant_id, test_id, body.variant, body.sent_delta, body.converted_delta)
    if test is None:
        raise HTTPException(status_code=404, detail="A/B test not found")
    return test


@router.post("/{test_id}/evaluate", response_model=ABTestOut)
async def evaluate_test(test_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: concludes the test and writes a winner in
    this same call if — and only if — the result is statistically
    significant with enough data. No human declares a winner."""
    test = await ab_test_engine.evaluate_test(db, tenant_id, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="A/B test not found")
    return test
