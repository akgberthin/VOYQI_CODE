import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import InvoiceStatus
from src.services import billing_engine

router = APIRouter(prefix="/api/v1/subscription", tags=["billing"])


class InvoiceCreate(BaseModel):
    member_profile_id: uuid.UUID
    subscription_id: uuid.UUID
    amount_usd: float
    billing_period_start: datetime
    billing_period_end: datetime
    proration_credit_usd: float = 0.0
    tax_jurisdiction: str = ""


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_profile_id: uuid.UUID
    subscription_id: uuid.UUID
    amount_usd: float
    proration_credit_usd: float
    status: InvoiceStatus
    retry_count: int
    tax_jurisdiction: str


class RefundRequest(BaseModel):
    amount_usd: float
    reason: str
    issued_by: str


@router.post("/invoices", response_model=InvoiceOut, status_code=201)
async def create_invoice(body: InvoiceCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await billing_engine.create_invoice(
        db, tenant_id=tenant_id, member_profile_id=body.member_profile_id, subscription_id=body.subscription_id,
        amount_usd=body.amount_usd, billing_period_start=body.billing_period_start, billing_period_end=body.billing_period_end,
        proration_credit_usd=body.proration_credit_usd, tax_jurisdiction=body.tax_jurisdiction,
    )


@router.post("/invoices/{invoice_id}/mark-paid", response_model=InvoiceOut)
async def mark_paid(invoice_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    invoice = await billing_engine.mark_paid(db, tenant_id, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.post("/invoices/{invoice_id}/payment-failed", response_model=InvoiceOut)
async def record_payment_failure(invoice_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    invoice = await billing_engine.record_payment_failure(db, tenant_id, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.post("/invoices/{invoice_id}/refund")
async def issue_refund(invoice_id: uuid.UUID, body: RefundRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    refund = await billing_engine.issue_refund(db, tenant_id, invoice_id, body.amount_usd, body.reason, body.issued_by)
    if refund is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"id": str(refund.id), "amount_usd": refund.amount_usd}


@router.get("/members/{member_profile_id}/invoices", response_model=list[InvoiceOut])
async def list_invoices(member_profile_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await billing_engine.list_invoices_for_member(db, tenant_id, member_profile_id)
