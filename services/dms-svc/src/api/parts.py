from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import parts_engine

router = APIRouter(prefix="/api/v1/dms/parts", tags=["parts"])


class PartCreate(BaseModel):
    part_number: str
    name: str
    unit_cost_usd: float
    stock_qty: int = 0
    reorder_point: int = 5


class PartOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    part_number: str
    name: str
    unit_cost_usd: float
    stock_qty: int
    reorder_point: int
    last_reorder_stock_qty: int | None


@router.post("", response_model=PartOut, status_code=201)
async def create_part(body: PartCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await parts_engine.create_part(db, tenant_id, body.part_number, body.name, body.unit_cost_usd, body.stock_qty, body.reorder_point)


@router.get("", response_model=list[PartOut])
async def list_parts(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await parts_engine.list_parts(db, tenant_id)


@router.post("/evaluate-reorder", response_model=list[PartOut])
async def evaluate_reorder(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: places a vendor order the instant stock is
    at or below the reorder point — no 'place order' click."""
    return await parts_engine.evaluate_and_reorder(db, tenant_id)
