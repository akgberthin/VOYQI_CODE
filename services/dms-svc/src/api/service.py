import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import service_engine

router = APIRouter(prefix="/api/v1/dms/service", tags=["service"])


class RepairOrderCreate(BaseModel):
    rooftop_id: uuid.UUID
    vin: str
    customer_name: str
    description: str
    warranty: bool = False


class RepairOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    rooftop_id: uuid.UUID
    vin: str
    customer_name: str
    description: str
    tech: str
    warranty: bool
    revenue_usd: float
    status: str
    estimated_duration_days: int | None


@router.post("/repair-orders", response_model=RepairOrderOut, status_code=201)
async def create_and_dispatch_ro(body: RepairOrderCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: opening the RO immediately dispatches a tech
    and estimates duration — never left 'open, awaiting dispatch'. A
    multi-day estimate auto-chains into a loaner assignment."""
    return await service_engine.create_and_dispatch_ro(db, tenant_id, body.rooftop_id, body.vin, body.customer_name, body.description, body.warranty)


@router.get("/repair-orders", response_model=list[RepairOrderOut])
async def list_repair_orders(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await service_engine.list_repair_orders(db, tenant_id)


@router.get("/repair-orders/{ro_id}", response_model=RepairOrderOut)
async def get_repair_order(ro_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    ro = await service_engine.get_repair_order(db, tenant_id, ro_id)
    if ro is None:
        raise HTTPException(status_code=404, detail="Repair order not found")
    return ro


@router.post("/repair-orders/{ro_id}/complete", response_model=RepairOrderOut)
async def complete_ro(ro_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await service_engine.complete_ro(db, tenant_id, ro_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
