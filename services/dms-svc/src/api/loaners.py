import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import loaner_engine

router = APIRouter(prefix="/api/v1/dms/loaners", tags=["loaners"])


class LoanerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    repair_order_id: uuid.UUID
    customer_name: str
    vehicle_label: str
    days_out: int
    status: str
    billing_flag: bool


@router.get("", response_model=list[LoanerOut])
async def list_loaners(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await loaner_engine.list_loaners(db, tenant_id)


@router.post("/{loaner_id}/return", response_model=LoanerOut)
async def return_loaner(loaner_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await loaner_engine.return_loaner(db, tenant_id, loaner_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/evaluate-overdue", response_model=list[LoanerOut])
async def evaluate_overdue(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: flags extended loaners for billing handoff
    the instant they cross the threshold."""
    return await loaner_engine.evaluate_overdue_loaners(db, tenant_id)
