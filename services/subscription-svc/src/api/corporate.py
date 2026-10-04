import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import corporate_engine

router = APIRouter(prefix="/api/v1/subscription/corporate", tags=["corporate"])


class CorporateAccountCreate(BaseModel):
    company_name: str
    billing_contact_email: str
    seat_limit: int = 10
    billing_type: str = "centralized"


class CorporateAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    company_name: str
    billing_contact_email: str
    billing_type: str
    seat_limit: int
    status: str


@router.post("/accounts", response_model=CorporateAccountOut, status_code=201)
async def create_corporate_account(body: CorporateAccountCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await corporate_engine.create_corporate_account(db, tenant_id, body.company_name, body.billing_contact_email, body.seat_limit, body.billing_type)


@router.get("/accounts", response_model=list[CorporateAccountOut])
async def list_corporate_accounts(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await corporate_engine.list_corporate_accounts(db, tenant_id)


@router.get("/accounts/{organization_id}", response_model=CorporateAccountOut)
async def get_corporate_account(organization_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    account = await corporate_engine.get_corporate_account(db, tenant_id, organization_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Corporate account not found")
    return account


@router.post("/accounts/{organization_id}/suspend", response_model=CorporateAccountOut)
async def suspend_corporate_account(organization_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    account = await corporate_engine.suspend_corporate_account(db, tenant_id, organization_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Corporate account not found")
    return account


@router.post("/accounts/{organization_id}/reactivate", response_model=CorporateAccountOut)
async def reactivate_corporate_account(organization_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    account = await corporate_engine.reactivate_corporate_account(db, tenant_id, organization_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Corporate account not found")
    return account


@router.get("/accounts/{organization_id}/billing-summary")
async def get_billing_summary(organization_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await corporate_engine.get_billing_summary(db, tenant_id, organization_id)


class EmployeeAdd(BaseModel):
    user_id: str
    full_name: str
    email: str


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: str
    full_name: str
    email: str
    member_type: str
    organization_id: uuid.UUID | None
    mobility_class: str | None
    service_level: str | None
    gu_balance: int
    lifetime_value_usd: float


@router.post("/accounts/{organization_id}/employees", response_model=EmployeeOut, status_code=201)
async def add_employee(organization_id: uuid.UUID, body: EmployeeAdd, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Refuses with 409 once the account is at its seat limit — a
    hard business rule, not a soft warning the caller can proceed past."""
    try:
        return await corporate_engine.add_employee(db, tenant_id, organization_id, body.user_id, body.full_name, body.email)
    except corporate_engine.SeatLimitExceededError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/accounts/{organization_id}/employees", response_model=list[EmployeeOut])
async def list_employees(organization_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await corporate_engine.list_employees(db, tenant_id, organization_id)


@router.post("/employees/{member_profile_id}/remove", response_model=EmployeeOut)
async def remove_employee(member_profile_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    employee = await corporate_engine.remove_employee(db, tenant_id, member_profile_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


class UtilizationEvaluateRequest(BaseModel):
    trips_by_member: dict[str, int] = {}


class UtilizationFlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    organization_id: uuid.UUID
    member_profile_id: uuid.UUID
    flag_type: str
    trips_last_30d: int
    reasoning: str
    resolved: bool
    resolved_by: str | None


@router.post("/accounts/{organization_id}/evaluate-utilization", response_model=list[UtilizationFlagOut])
async def evaluate_utilization(organization_id: uuid.UUID, body: UtilizationEvaluateRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: scans every employee and writes a flag for
    anyone outside the normal utilization band in this same call."""
    return await corporate_engine.evaluate_utilization(db, tenant_id, organization_id, body.trips_by_member)


@router.get("/accounts/{organization_id}/utilization-flags", response_model=list[UtilizationFlagOut])
async def list_utilization_flags(organization_id: uuid.UUID, unresolved_only: bool = True, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await corporate_engine.list_utilization_flags(db, tenant_id, organization_id, unresolved_only)


class ResolveFlagRequest(BaseModel):
    resolved_by: str


@router.post("/utilization-flags/{flag_id}/resolve", response_model=UtilizationFlagOut)
async def resolve_utilization_flag(flag_id: uuid.UUID, body: ResolveFlagRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The corporate admin's own decision about their company's
    account and cost structure — this records that decision, it
    doesn't make one."""
    flag = await corporate_engine.resolve_utilization_flag(db, tenant_id, flag_id, body.resolved_by)
    if flag is None:
        raise HTTPException(status_code=404, detail="Utilization flag not found")
    return flag
