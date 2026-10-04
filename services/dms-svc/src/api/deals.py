import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import deal_engine

router = APIRouter(prefix="/api/v1/dms/deals", tags=["deals"])


class DealCreate(BaseModel):
    rooftop_id: uuid.UUID
    vehicle_id: uuid.UUID
    buyer_name: str


class DealOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    rooftop_id: uuid.UUID
    vehicle_id: uuid.UUID
    buyer_name: str
    price_usd: float
    status: str


@router.post("", response_model=DealOut, status_code=201)
async def create_deal(body: DealCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await deal_engine.create_deal(db, tenant_id, body.rooftop_id, body.vehicle_id, body.buyer_name)


@router.get("", response_model=list[DealOut])
async def list_deals(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await deal_engine.list_deals(db, tenant_id)


@router.get("/{deal_id}", response_model=DealOut)
async def get_deal(deal_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    deal = await deal_engine.get_deal(db, tenant_id, deal_id)
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal


@router.post("/{deal_id}/desk-and-approve", response_model=DealOut)
async def desk_and_approve(deal_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: desking and approval happen in one call. A
    price missing the gross floor is auto-countered upward and
    approved in the same pass — never queued for a manager."""
    try:
        return await deal_engine.desk_and_approve(db, tenant_id, deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{deal_id}/deliver", response_model=DealOut)
async def deliver_deal(deal_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await deal_engine.deliver_deal(db, tenant_id, deal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
