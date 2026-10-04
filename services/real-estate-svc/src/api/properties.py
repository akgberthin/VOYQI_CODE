import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import PropertyStatus, PropertyType
from src.services import property_engine

router = APIRouter(prefix="/api/v1/real-estate/properties", tags=["properties"])


class PropertyIn(BaseModel):
    key: str
    name: str
    property_type: PropertyType


class PropertyOut(BaseModel):
    id: str
    key: str
    name: str
    property_type: PropertyType
    status: PropertyStatus


def _to_out(p) -> PropertyOut:
    return PropertyOut(id=str(p.id), key=p.key, name=p.name, property_type=p.property_type, status=p.status)


@router.post("", response_model=PropertyOut, status_code=201)
async def create_property(
    payload: PropertyIn,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> PropertyOut:
    prop = await property_engine.create_property(db, tenant_id, payload.key, payload.name, payload.property_type)
    return _to_out(prop)


@router.get("", response_model=list[PropertyOut])
async def list_properties(
    property_type: PropertyType | None = None,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[PropertyOut]:
    props = await property_engine.list_properties(db, tenant_id, property_type)
    return [_to_out(p) for p in props]


@router.delete("/{property_id}", response_model=PropertyOut)
async def deactivate_property(
    property_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> PropertyOut:
    prop = await property_engine.deactivate_property(db, tenant_id, property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return _to_out(prop)
