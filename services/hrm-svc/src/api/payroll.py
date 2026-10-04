import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import GarnishmentType, PayRunStatus
from src.services import payroll_engine

router = APIRouter(prefix="/api/v1/hrm", tags=["payroll"])


class PayRunCreate(BaseModel):
    entity_ref: str
    pay_period_start: datetime
    pay_period_end: datetime


class PayRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_ref: str
    pay_period_start: datetime
    pay_period_end: datetime
    status: PayRunStatus
    total_gross_usd: float
    total_net_usd: float
    total_tax_usd: float
    processed_at: datetime | None


class PayStubCreate(BaseModel):
    employee_id: uuid.UUID
    gross_pay_usd: float


class PayStubOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pay_run_id: uuid.UUID
    employee_id: uuid.UUID
    gross_pay_usd: float
    fica_usd: float
    federal_tax_usd: float
    state_tax_usd: float
    garnishment_usd: float
    benefits_premium_usd: float
    net_pay_usd: float


class TaxStatusUpdate(BaseModel):
    work_state: str
    residence_state: str
    filing_status: str = "single"


class GarnishmentCreate(BaseModel):
    order_type: GarnishmentType
    amount_per_period_usd: float


@router.post("/pay-runs", response_model=PayRunOut, status_code=201)
async def create_pay_run(body: PayRunCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await payroll_engine.create_pay_run(db, tenant_id, body.entity_ref, body.pay_period_start, body.pay_period_end)


@router.get("/pay-runs", response_model=list[PayRunOut])
async def list_pay_runs(entity_ref: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await payroll_engine.list_pay_runs(db, tenant_id, entity_ref=entity_ref)


@router.get("/pay-runs/{pay_run_id}", response_model=PayRunOut)
async def get_pay_run(pay_run_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    run = await payroll_engine.get_pay_run(db, tenant_id, pay_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pay run not found")
    return run


@router.post("/pay-runs/{pay_run_id}/pay-stubs", response_model=PayStubOut, status_code=201)
async def add_pay_stub(pay_run_id: uuid.UUID, body: PayStubCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await payroll_engine.add_pay_stub(db, tenant_id, pay_run_id, body.employee_id, body.gross_pay_usd)


@router.get("/pay-runs/{pay_run_id}/pay-stubs", response_model=list[PayStubOut])
async def list_pay_stubs(pay_run_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await payroll_engine.list_pay_stubs(db, tenant_id, pay_run_id)


@router.post("/pay-runs/{pay_run_id}/process", response_model=PayRunOut)
async def process_pay_run(pay_run_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    run = await payroll_engine.process_pay_run(db, tenant_id, pay_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pay run not found")
    return run


@router.post("/employees/{employee_id}/tax-status")
async def set_tax_status(employee_id: uuid.UUID, body: TaxStatusUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    status = await payroll_engine.set_tax_status(db, tenant_id, employee_id, body.work_state, body.residence_state, body.filing_status)
    return {"employee_id": str(employee_id), "work_state": status.work_state, "residence_state": status.residence_state, "is_multi_state": status.is_multi_state}


@router.post("/employees/{employee_id}/garnishments", status_code=201)
async def create_garnishment_order(employee_id: uuid.UUID, body: GarnishmentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    order = await payroll_engine.create_garnishment_order(db, tenant_id, employee_id, body.order_type, body.amount_per_period_usd)
    return {"id": str(order.id), "order_type": order.order_type.value, "amount_per_period_usd": order.amount_per_period_usd, "is_active": order.is_active}


@router.get("/employees/{employee_id}/garnishments")
async def list_garnishment_orders(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    orders = await payroll_engine.list_garnishment_orders(db, tenant_id, employee_id)
    return [{"id": str(o.id), "order_type": o.order_type.value, "amount_per_period_usd": o.amount_per_period_usd, "is_active": o.is_active} for o in orders]


@router.post("/garnishments/{order_id}/deactivate")
async def deactivate_garnishment_order(order_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    order = await payroll_engine.deactivate_garnishment_order(db, tenant_id, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Garnishment order not found")
    return {"id": str(order.id), "is_active": order.is_active}
