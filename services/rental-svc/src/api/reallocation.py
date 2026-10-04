import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import reallocation_engine

router = APIRouter(prefix="/api/v1/rental/reallocation", tags=["reallocation"])


class ExecuteRequest(BaseModel):
    vehicle_id: uuid.UUID
    new_hub_id: str


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    hub_id: str
    rental_class: str
    vin: str
    status: str


@router.post("/execute", response_model=VehicleOut)
async def execute_reallocation(body: ExecuteRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The administrator's own action, taken after reviewing a
    suggestion from GET /api/v1/rental/hubs/reallocation-opportunities
    — this records the physical move, it doesn't decide to make it."""
    vehicle = await reallocation_engine.execute_reallocation(db, tenant_id, body.vehicle_id, body.new_hub_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle
