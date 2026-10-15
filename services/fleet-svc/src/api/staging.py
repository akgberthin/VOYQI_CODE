import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_ai_initiated, get_tenant_id
from src.models import Hub, TransferOrderStatus
from src.services import staging_engine

router = APIRouter(prefix="/api/v1/fleet", tags=["staging"])


class HubCreate(BaseModel):
    name: str
    region: str = ""
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    geofence_radius_miles: float | None = Field(default=None, gt=0)


class HubOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    region: str
    latitude: float | None = None
    longitude: float | None = None
    geofence_radius_miles: float | None = None


class TransferCreate(BaseModel):
    vehicle_id: uuid.UUID
    to_hub_id: uuid.UUID
    reason: str
    generated_by_agent_key: str | None = None


class TransferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    from_hub_id: uuid.UUID | None
    to_hub_id: uuid.UUID
    status: TransferOrderStatus
    reason: str
    ai_initiated: bool
    dispatched_by: str | None
    driver_id: str | None
    created_at: datetime


class DispatchRequest(BaseModel):
    dispatched_by: str
    driver_id: str


@router.post("/hubs", response_model=HubOut, status_code=201)
async def create_hub(body: HubCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await staging_engine.create_hub(
        db, tenant_id=tenant_id, name=body.name, region=body.region,
        latitude=body.latitude, longitude=body.longitude, geofence_radius_miles=body.geofence_radius_miles,
    )


@router.get("/hubs", response_model=list[HubOut])
async def list_hubs(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Hub).where(Hub.tenant_id == tenant_id))
    return result.scalars().all()


@router.get("/hubs/{hub_id}/supply")
async def get_hub_supply(hub_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await staging_engine.get_hub_supply(db, tenant_id, hub_id)


@router.post("/transfer-orders", response_model=TransferOut, status_code=201)
async def propose_transfer(
    body: TransferCreate,
    tenant_id: str = Depends(get_tenant_id),
    ai_initiated_header: str | None = Depends(get_ai_initiated),
    db: AsyncSession = Depends(get_db),
):
    try:
        order = await staging_engine.propose_transfer(
            db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, to_hub_id=body.to_hub_id,
            reason=body.reason, ai_initiated=bool(ai_initiated_header),
            generated_by_agent_key=body.generated_by_agent_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return order


class AutoDispatchTransferRequest(BaseModel):
    vehicle_id: uuid.UUID
    to_hub_id: uuid.UUID
    reason: str
    agent_key: str


@router.post("/transfer-orders/auto-dispatch", response_model=TransferOut, status_code=201)
async def auto_dispatch_transfer(
    body: AutoDispatchTransferRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Fleet Intelligence Agent's fully autonomous staging action —
    creates and dispatches a transfer in one call, no human gate."""
    try:
        order = await staging_engine.auto_dispatch_transfer(
            db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, to_hub_id=body.to_hub_id,
            reason=body.reason, agent_key=body.agent_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return order


@router.get("/transfer-orders", response_model=list[TransferOut])
async def list_transfers(status: TransferOrderStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await staging_engine.list_transfers(db, tenant_id, status=status)


@router.post("/transfer-orders/{transfer_id}/dispatch", response_model=TransferOut)
async def dispatch_transfer(transfer_id: uuid.UUID, body: DispatchRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    order = await staging_engine.dispatch_transfer(db, tenant_id, transfer_id, body.dispatched_by, body.driver_id)
    if order is None:
        raise HTTPException(status_code=409, detail="Transfer order not found or not in 'proposed' status")
    return order


@router.post("/transfer-orders/{transfer_id}/complete", response_model=TransferOut)
async def complete_transfer(transfer_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    order = await staging_engine.complete_transfer(db, tenant_id, transfer_id)
    if order is None:
        raise HTTPException(status_code=409, detail="Transfer order not found or not in 'dispatched' status")
    return order


@router.post("/transfer-orders/{transfer_id}/cancel", response_model=TransferOut)
async def cancel_transfer(transfer_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    order = await staging_engine.cancel_transfer(db, tenant_id, transfer_id)
    if order is None:
        raise HTTPException(status_code=409, detail="Transfer order not found or already finished")
    return order
