import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import recon_engine

router = APIRouter(prefix="/api/v1/dms/recon", tags=["recon"])


class ReconOrderCreate(BaseModel):
    vehicle_id: uuid.UUID
    arrived_days_ago: int = 0


class ReconOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    vehicle_id: uuid.UUID
    arrived_days_ago: int
    status: str
    turnaround_days: int | None


@router.post("/orders", response_model=ReconOrderOut, status_code=201)
async def create_recon_order(body: ReconOrderCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recon_engine.create_recon_order(db, tenant_id, body.vehicle_id, body.arrived_days_ago)


@router.get("/orders", response_model=list[ReconOrderOut])
async def list_recon_orders(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await recon_engine.list_recon_orders(db, tenant_id)


@router.get("/orders/{order_id}", response_model=ReconOrderOut)
async def get_recon_order(order_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    order = await recon_engine.get_recon_order(db, tenant_id, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Recon order not found")
    return order


class WorkItemCreate(BaseModel):
    category: str
    description: str
    cost_usd: float = 0.0


class WorkItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    recon_order_id: uuid.UUID
    category: str
    description: str
    cost_usd: float
    assigned_to: str
    status: str


@router.post("/orders/{order_id}/work-items", response_model=WorkItemOut, status_code=201)
async def add_work_item(order_id: uuid.UUID, body: WorkItemCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous assignment by category — never 'unassigned'."""
    try:
        return await recon_engine.add_work_item(db, tenant_id, order_id, body.category, body.description, body.cost_usd)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/work-items/{item_id}/complete", response_model=WorkItemOut)
async def complete_work_item(item_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Marking an item done is a technician's real-world report, not
    a business decision. But the moment every item on the order is
    done, the order auto-advances to front-line-ready in this same
    call — no manager click."""
    try:
        item, _order = await recon_engine.complete_work_item(db, tenant_id, item_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return item
