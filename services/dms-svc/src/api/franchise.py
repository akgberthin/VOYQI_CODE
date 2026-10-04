import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import franchise_engine

router = APIRouter(prefix="/api/v1/dms/franchise", tags=["franchise"])


class FranchiseCreate(BaseModel):
    rooftop_id: uuid.UUID
    status: str
    score: int
    coop_usd: float = 0.0
    rebrand_status: str = "not_applicable"
    notes: str = ""


class FranchiseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    rooftop_id: uuid.UUID
    status: str
    score: int
    coop_usd: float
    rebrand_status: str
    notes: str
    remediation_opened: bool


@router.post("", response_model=FranchiseOut, status_code=201)
async def create_franchise_record(body: FranchiseCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await franchise_engine.create_franchise_record(db, tenant_id, body.rooftop_id, body.status, body.score, body.coop_usd, body.rebrand_status, body.notes)


@router.get("", response_model=list[FranchiseOut])
async def list_franchise_records(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await franchise_engine.list_franchise_records(db, tenant_id)


@router.post("/evaluate-remediation", response_model=list[FranchiseOut])
async def evaluate_remediation(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: opens a remediation task the instant a
    rooftop's status is non-compliant — no one has to spot it first."""
    return await franchise_engine.evaluate_and_remediate(db, tenant_id)
