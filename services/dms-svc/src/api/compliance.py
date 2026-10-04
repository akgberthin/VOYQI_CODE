import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import compliance_engine

router = APIRouter(prefix="/api/v1/dms/compliance-holds", tags=["compliance"])


class ComplianceHoldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    type: str
    label: str
    detail: str
    risk_score: int
    resolved: bool


@router.get("", response_model=list[ComplianceHoldOut])
async def list_open_holds(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """By design, this list only ever contains OFAC, Red Flags Rule,
    or stolen-vehicle matches — the three genuine security matters on
    this platform. Nothing else is ever written here."""
    return await compliance_engine.list_open_holds(db, tenant_id)


@router.post("/{hold_id}/resolve", response_model=ComplianceHoldOut)
async def resolve_hold(hold_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Human-only, by construction — there is no autonomous
    resolution path for any of the three hold types."""
    try:
        return await compliance_engine.resolve_hold(db, tenant_id, hold_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
