"""
HRM-002 Payroll & Benefits Administration — the payroll half, and it's
entirely deterministic, by design. Every dollar figure is a plain
formula, reproducible and auditable exactly the way a real paycheck
has to be — there is no AI reasoning anywhere in this module's math,
full stop, the same principle as ORG-004's e-contracting.

Being upfront about scope: FEDERAL_TAX_RATE below is a deliberately
simplified flat rate, not real IRS Publication 15-T withholding
tables (which depend on W-4 elections, pay frequency, and filing
status in ways a demo platform shouldn't pretend to replicate without
certified tax tables). FICA (7.65%) and the FL/TX zero state-income-tax
facts are real and correctly modeled; the federal rate is a clearly
labeled placeholder for the real thing.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    BenefitsEnrollment,
    BenefitsPlan,
    EnrollmentStatus,
    GarnishmentOrder,
    PayrollTaxStatus,
    PayRun,
    PayRunStatus,
    PayStub,
)

FICA_RATE = 0.0765  # 6.2% Social Security + 1.45% Medicare, employee portion — real, standard rate
FEDERAL_TAX_RATE = 0.12  # simplified flat rate — see module docstring
STATE_TAX_RATES = {"FL": 0.0, "TX": 0.0}  # both real: neither state levies personal income tax
DEFAULT_STATE_TAX_RATE = 0.05  # fallback for any other state, clearly a placeholder


async def get_or_create_tax_status(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> PayrollTaxStatus:
    result = await db.execute(select(PayrollTaxStatus).where(PayrollTaxStatus.tenant_id == tenant_id, PayrollTaxStatus.employee_id == employee_id))
    status = result.scalar_one_or_none()
    if status is None:
        status = PayrollTaxStatus(tenant_id=tenant_id, employee_id=employee_id)
        db.add(status)
        await db.commit()
        await db.refresh(status)
    return status


async def set_tax_status(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, work_state: str, residence_state: str, filing_status: str = "single") -> PayrollTaxStatus:
    status = await get_or_create_tax_status(db, tenant_id, employee_id)
    status.work_state = work_state.upper()
    status.residence_state = residence_state.upper()
    status.filing_status = filing_status
    status.is_multi_state = status.work_state != status.residence_state
    await db.commit()
    await db.refresh(status)
    return status


async def _active_garnishment_total(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> float:
    result = await db.execute(select(GarnishmentOrder).where(GarnishmentOrder.tenant_id == tenant_id, GarnishmentOrder.employee_id == employee_id, GarnishmentOrder.is_active))
    orders = list(result.scalars().all())
    return sum(o.amount_per_period_usd for o in orders)


async def _active_benefits_premium_total(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> float:
    result = await db.execute(select(BenefitsEnrollment).where(BenefitsEnrollment.tenant_id == tenant_id, BenefitsEnrollment.employee_id == employee_id, BenefitsEnrollment.status == EnrollmentStatus.ACTIVE))
    enrollments = list(result.scalars().all())
    total = 0.0
    for e in enrollments:
        plan_result = await db.execute(select(BenefitsPlan).where(BenefitsPlan.id == e.plan_id, BenefitsPlan.tenant_id == tenant_id))
        plan = plan_result.scalar_one_or_none()
        if plan:
            employee_share = plan.monthly_premium_usd * (1 - plan.employer_contribution_pct)
            # bi-weekly pay period assumption: monthly premium / 2 per check
            total += employee_share / 2
    return total


def compute_pay_stub_amounts(gross_pay_usd: float, work_state: str, garnishment_total: float, benefits_premium_total: float) -> dict:
    """The actual deterministic formula, pulled out as a pure function
    so it's directly unit-testable without any database round-trip."""
    fica = round(gross_pay_usd * FICA_RATE, 2)
    federal_tax = round(gross_pay_usd * FEDERAL_TAX_RATE, 2)
    state_rate = STATE_TAX_RATES.get(work_state.upper(), DEFAULT_STATE_TAX_RATE)
    state_tax = round(gross_pay_usd * state_rate, 2)
    net_pay = round(gross_pay_usd - fica - federal_tax - state_tax - garnishment_total - benefits_premium_total, 2)
    return {
        "fica_usd": fica, "federal_tax_usd": federal_tax, "state_tax_usd": state_tax,
        "garnishment_usd": round(garnishment_total, 2), "benefits_premium_usd": round(benefits_premium_total, 2),
        "net_pay_usd": net_pay,
    }


async def create_pay_run(db: AsyncSession, tenant_id: str, entity_ref: str, pay_period_start: datetime, pay_period_end: datetime) -> PayRun:
    run = PayRun(tenant_id=tenant_id, entity_ref=entity_ref, pay_period_start=pay_period_start, pay_period_end=pay_period_end)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def add_pay_stub(db: AsyncSession, tenant_id: str, pay_run_id: uuid.UUID, employee_id: uuid.UUID, gross_pay_usd: float) -> PayStub:
    tax_status = await get_or_create_tax_status(db, tenant_id, employee_id)
    garnishment_total = await _active_garnishment_total(db, tenant_id, employee_id)
    benefits_total = await _active_benefits_premium_total(db, tenant_id, employee_id)
    amounts = compute_pay_stub_amounts(gross_pay_usd, tax_status.work_state, garnishment_total, benefits_total)

    stub = PayStub(tenant_id=tenant_id, pay_run_id=pay_run_id, employee_id=employee_id, gross_pay_usd=gross_pay_usd, **amounts)
    db.add(stub)
    await db.commit()
    await db.refresh(stub)
    return stub


async def process_pay_run(db: AsyncSession, tenant_id: str, pay_run_id: uuid.UUID) -> PayRun | None:
    result = await db.execute(select(PayRun).where(PayRun.id == pay_run_id, PayRun.tenant_id == tenant_id))
    run = result.scalar_one_or_none()
    if run is None:
        return None

    stubs_result = await db.execute(select(PayStub).where(PayStub.tenant_id == tenant_id, PayStub.pay_run_id == pay_run_id))
    stubs = list(stubs_result.scalars().all())

    run.total_gross_usd = round(sum(s.gross_pay_usd for s in stubs), 2)
    run.total_net_usd = round(sum(s.net_pay_usd for s in stubs), 2)
    run.total_tax_usd = round(sum(s.fica_usd + s.federal_tax_usd + s.state_tax_usd for s in stubs), 2)
    run.status = PayRunStatus.COMPLETED
    run.processed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(run)
    return run


async def get_pay_run(db: AsyncSession, tenant_id: str, pay_run_id: uuid.UUID) -> PayRun | None:
    result = await db.execute(select(PayRun).where(PayRun.id == pay_run_id, PayRun.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_pay_runs(db: AsyncSession, tenant_id: str, entity_ref: str | None = None) -> list[PayRun]:
    stmt = select(PayRun).where(PayRun.tenant_id == tenant_id)
    if entity_ref:
        stmt = stmt.where(PayRun.entity_ref == entity_ref)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_pay_stubs(db: AsyncSession, tenant_id: str, pay_run_id: uuid.UUID) -> list[PayStub]:
    result = await db.execute(select(PayStub).where(PayStub.tenant_id == tenant_id, PayStub.pay_run_id == pay_run_id))
    return list(result.scalars().all())


async def create_garnishment_order(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, order_type, amount_per_period_usd: float) -> GarnishmentOrder:
    order = GarnishmentOrder(tenant_id=tenant_id, employee_id=employee_id, order_type=order_type, amount_per_period_usd=amount_per_period_usd)
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


async def deactivate_garnishment_order(db: AsyncSession, tenant_id: str, order_id: uuid.UUID) -> GarnishmentOrder | None:
    result = await db.execute(select(GarnishmentOrder).where(GarnishmentOrder.id == order_id, GarnishmentOrder.tenant_id == tenant_id))
    order = result.scalar_one_or_none()
    if order is None:
        return None
    order.is_active = False
    order.end_date = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(order)
    return order


async def list_garnishment_orders(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[GarnishmentOrder]:
    result = await db.execute(select(GarnishmentOrder).where(GarnishmentOrder.tenant_id == tenant_id, GarnishmentOrder.employee_id == employee_id))
    return list(result.scalars().all())
