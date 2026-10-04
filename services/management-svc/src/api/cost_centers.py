import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import CostCenterCategory, CostCenterStatus
from src.services import cost_center_engine

router = APIRouter(prefix="/api/v1/management/cost-centers", tags=["cost-centers"])


class CostCenterIn(BaseModel):
    key: str
    name: str
    category: CostCenterCategory


class CostCenterOut(BaseModel):
    id: str
    key: str
    name: str
    category: CostCenterCategory
    status: CostCenterStatus


def _to_out(c) -> CostCenterOut:
    return CostCenterOut(id=str(c.id), key=c.key, name=c.name, category=c.category, status=c.status)


@router.post("", response_model=CostCenterOut, status_code=201)
async def create_cost_center(
    payload: CostCenterIn,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> CostCenterOut:
    cost_center = await cost_center_engine.create_cost_center(
        db, tenant_id, payload.key, payload.name, payload.category
    )
    return _to_out(cost_center)


@router.get("", response_model=list[CostCenterOut])
async def list_cost_centers(
    category: CostCenterCategory | None = None,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[CostCenterOut]:
    cost_centers = await cost_center_engine.list_cost_centers(db, tenant_id, category)
    return [_to_out(c) for c in cost_centers]


@router.delete("/{cost_center_id}", response_model=CostCenterOut)
async def deactivate_cost_center(
    cost_center_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> CostCenterOut:
    cost_center = await cost_center_engine.deactivate_cost_center(db, tenant_id, cost_center_id)
    if cost_center is None:
        raise HTTPException(status_code=404, detail="Cost center not found")
    return _to_out(cost_center)
