import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import HubType
from src.services import hub_engine

router = APIRouter(prefix="/api/v1/hub", tags=["hub-directory"])


class HubCreate(BaseModel):
    name: str
    hub_type: HubType
    latitude: float
    longitude: float
    address: str = ""
    capacity: int = 10
    operating_hours_open: str = "06:00"
    operating_hours_close: str = "22:00"
    has_lounge: bool = False
    amenities: list[str] = []
    tenant_entity_ref: str = ""


class HubOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    hub_type: HubType
    address: str
    latitude: float
    longitude: float
    capacity: int
    operating_hours_open: str
    operating_hours_close: str
    has_lounge: bool
    amenities: list
    tenant_entity_ref: str
    is_active: bool


class CapacityUpdate(BaseModel):
    capacity: int


@router.post("/hubs", response_model=HubOut, status_code=201)
async def create_hub(body: HubCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await hub_engine.create_hub(
        db, tenant_id, body.name, body.hub_type, body.latitude, body.longitude,
        address=body.address, capacity=body.capacity, operating_hours_open=body.operating_hours_open,
        operating_hours_close=body.operating_hours_close, has_lounge=body.has_lounge,
        amenities=body.amenities, tenant_entity_ref=body.tenant_entity_ref,
    )


@router.get("/hubs", response_model=list[HubOut])
async def list_hubs(
    hub_type: HubType | None = None, is_active: bool | None = None,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    return await hub_engine.list_hubs(db, tenant_id, hub_type=hub_type, is_active=is_active)


@router.get("/hubs/{hub_id}", response_model=HubOut)
async def get_hub(hub_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    hub = await hub_engine.get_hub(db, tenant_id, hub_id)
    if hub is None:
        raise HTTPException(status_code=404, detail="Hub not found")
    return hub


@router.patch("/hubs/{hub_id}/capacity", response_model=HubOut)
async def update_capacity(hub_id: uuid.UUID, body: CapacityUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    hub = await hub_engine.update_capacity(db, tenant_id, hub_id, body.capacity)
    if hub is None:
        raise HTTPException(status_code=404, detail="Hub not found")
    return hub


@router.post("/hubs/{hub_id}/deactivate", response_model=HubOut)
async def deactivate_hub(hub_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    hub = await hub_engine.deactivate_hub(db, tenant_id, hub_id)
    if hub is None:
        raise HTTPException(status_code=404, detail="Hub not found")
    return hub
