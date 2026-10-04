import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import MobilityClass, SwapRequestStatus
from src.services import swap_engine

router = APIRouter(prefix="/api/v1/subscription", tags=["vehicle-swap"])


class SwapRequestCreate(BaseModel):
    member_profile_id: uuid.UUID
    requested_class: MobilityClass


class SwapRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_profile_id: uuid.UUID
    requested_class: MobilityClass
    status: SwapRequestStatus
    fleet_transfer_order_id: uuid.UUID | None
    sla_due_at: datetime


class StageRequest(BaseModel):
    to_hub_id: str


@router.post("/swap-requests", response_model=SwapRequestOut, status_code=201)
async def request_swap(body: SwapRequestCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await swap_engine.request_swap(db, tenant_id, body.member_profile_id, body.requested_class)


@router.post("/swap-requests/{swap_request_id}/stage", response_model=SwapRequestOut)
async def stage_fulfillment(swap_request_id: uuid.UUID, body: StageRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    request = await swap_engine.stage_fulfillment(db, tenant_id, swap_request_id, body.to_hub_id)
    if request is None:
        raise HTTPException(status_code=409, detail="Swap request not found or not in 'requested' status")
    return request


@router.post("/swap-requests/{swap_request_id}/fulfill", response_model=SwapRequestOut)
async def fulfill_swap(swap_request_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    request = await swap_engine.fulfill_swap(db, tenant_id, swap_request_id)
    if request is None:
        raise HTTPException(status_code=409, detail="Swap request not found or not in 'staged' status")
    return request


@router.post("/swap-requests/{swap_request_id}/check-sla", response_model=SwapRequestOut)
async def check_sla(swap_request_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    request = await swap_engine.check_sla_breach(db, tenant_id, swap_request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Swap request not found")
    return request


@router.get("/swap-requests", response_model=list[SwapRequestOut])
async def list_swap_requests(status: SwapRequestStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await swap_engine.list_swap_requests(db, tenant_id, status=status)
