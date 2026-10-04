"""
MOB-004 Billing & Invoicing Console engine.

Dunning: a failed payment increments retry_count; past
MAX_DUNNING_RETRIES the invoice stays PAST_DUE for a human collections
workflow rather than being retried forever — an infinite retry loop on
a card that's never going to work is its own kind of bug.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Invoice, InvoiceStatus, Refund

MAX_DUNNING_RETRIES = 3


async def create_invoice(
    db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, subscription_id: uuid.UUID,
    amount_usd: float, billing_period_start: datetime, billing_period_end: datetime,
    proration_credit_usd: float = 0.0, tax_jurisdiction: str = "",
) -> Invoice:
    invoice = Invoice(
        tenant_id=tenant_id, member_profile_id=member_profile_id, subscription_id=subscription_id,
        amount_usd=amount_usd, proration_credit_usd=proration_credit_usd, tax_jurisdiction=tax_jurisdiction,
        billing_period_start=billing_period_start, billing_period_end=billing_period_end,
    )
    db.add(invoice)
    await db.commit()
    await db.refresh(invoice)
    return invoice


async def net_amount_due(invoice: Invoice) -> float:
    return max(0.0, invoice.amount_usd - invoice.proration_credit_usd)


async def mark_paid(db: AsyncSession, tenant_id: str, invoice_id: uuid.UUID) -> Invoice | None:
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id))
    invoice = result.scalar_one_or_none()
    if invoice is None:
        return None
    invoice.status = InvoiceStatus.PAID
    invoice.paid_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(invoice)
    return invoice


async def record_payment_failure(db: AsyncSession, tenant_id: str, invoice_id: uuid.UUID) -> Invoice | None:
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id))
    invoice = result.scalar_one_or_none()
    if invoice is None:
        return None
    invoice.retry_count += 1
    if invoice.retry_count > MAX_DUNNING_RETRIES:
        invoice.status = InvoiceStatus.PAST_DUE
    await db.commit()
    await db.refresh(invoice)
    return invoice


async def issue_refund(db: AsyncSession, tenant_id: str, invoice_id: uuid.UUID, amount_usd: float, reason: str, issued_by: str) -> Refund | None:
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id))
    invoice = result.scalar_one_or_none()
    if invoice is None:
        return None
    refund = Refund(tenant_id=tenant_id, invoice_id=invoice_id, amount_usd=amount_usd, reason=reason, issued_by=issued_by)
    db.add(refund)
    invoice.status = InvoiceStatus.REFUNDED
    await db.commit()
    await db.refresh(refund)
    return refund


async def list_invoices_for_member(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID) -> list[Invoice]:
    result = await db.execute(
        select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.member_profile_id == member_profile_id)
    )
    return list(result.scalars().all())
