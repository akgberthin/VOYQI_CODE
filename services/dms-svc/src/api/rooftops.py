import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import rooftop_engine

router = APIRouter(prefix="/api/v1/dms/rooftops", tags=["rooftops"])


class RooftopCreate(BaseModel):
    name: str
    holding_company: str = ""
    oem_brand: str
    license_number: str
    license_expiry: datetime


class RooftopOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    holding_company: str
    oem_brand: str
    license_number: str
    license_expiry: datetime
    renewal_filed: bool


@router.post("", response_model=RooftopOut, status_code=201)
async def create_rooftop(body: RooftopCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await rooftop_engine.create_rooftop(db, tenant_id, body.name, body.holding_company, body.oem_brand, body.license_number, body.license_expiry)


@router.get("", response_model=list[RooftopOut])
async def list_rooftops(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await rooftop_engine.list_rooftops(db, tenant_id)


@router.get("/{rooftop_id}", response_model=RooftopOut)
async def get_rooftop(rooftop_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    rooftop = await rooftop_engine.get_rooftop(db, tenant_id, rooftop_id)
    if rooftop is None:
        raise HTTPException(status_code=404, detail="Rooftop not found")
    return rooftop


@router.post("/evaluate-renewals", response_model=list[RooftopOut])
async def evaluate_renewals(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: files a renewal for any rooftop within the
    window that hasn't already been filed, in this same call."""
    return await rooftop_engine.evaluate_and_file_renewals(db, tenant_id)
