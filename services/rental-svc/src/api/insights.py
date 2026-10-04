import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import insight_engine

router = APIRouter(prefix="/api/v1/rental/insights", tags=["insights"])


class ClientInsightOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    hub_id: str
    rental_class: str
    insight_type: str
    message: str


class AdminInsightOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    hub_id: str
    rental_class: str
    insight_type: str
    severity: str
    message: str
    resolved: bool


@router.post("/client/generate", response_model=list[ClientInsightOut])
async def generate_client_insights(hub_id: str, rental_class: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Instructive to the renter — fully autonomous, derived from the
    same forecast data the pricing engine reads."""
    return await insight_engine.generate_client_insights(db, tenant_id, hub_id, rental_class)


@router.get("/client", response_model=list[ClientInsightOut])
async def list_client_insights(hub_id: str | None = None, rental_class: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await insight_engine.list_client_insights(db, tenant_id, hub_id, rental_class)


@router.post("/admin/generate", response_model=list[AdminInsightOut])
async def generate_admin_insights(hub_id: str, rental_class: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Instructive to the hub administrator — a predictive, actionable
    flag rather than a raw metric."""
    return await insight_engine.generate_admin_insights(db, tenant_id, hub_id, rental_class)


@router.get("/admin", response_model=list[AdminInsightOut])
async def list_admin_insights(hub_id: str | None = None, resolved: bool | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await insight_engine.list_admin_insights(db, tenant_id, hub_id, resolved)


@router.post("/admin/{insight_id}/resolve", response_model=AdminInsightOut)
async def resolve_admin_insight(insight_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    insight = await insight_engine.resolve_admin_insight(db, tenant_id, insight_id)
    if insight is None:
        raise HTTPException(status_code=404, detail="Insight not found")
    return insight
