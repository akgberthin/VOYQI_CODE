"""
DMS-008 — Loaner & Courtesy Vehicle Manager engine.

auto_assign_loaner is called directly from service_engine.py's RO
dispatch — not a separate reservation step. evaluate_overdue_loaners
auto-flags extended loaners for billing handoff the instant they cross
the threshold.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import Loaner, LoanerStatus

LOANER_FLEET = [
    "VOYQI Urban loaner #1", "VOYQI Urban loaner #2", "VOYQI Urban loaner #3",
    "VOYQI Urban loaner #4", "VOYQI Family loaner #1",
]


async def list_loaners(db: AsyncSession, tenant_id: str) -> list[Loaner]:
    result = await db.execute(select(Loaner).where(Loaner.tenant_id == tenant_id))
    return list(result.scalars().all())


async def auto_assign_loaner(db: AsyncSession, tenant_id: str, repair_order_id: uuid.UUID, customer_name: str, estimated_duration_days: int) -> Loaner:
    existing = await list_loaners(db, tenant_id)
    in_use = {l.vehicle_label for l in existing if l.status != LoanerStatus.RETURNED}
    available = next((v for v in LOANER_FLEET if v not in in_use), None)

    loaner = Loaner(
        tenant_id=tenant_id, repair_order_id=repair_order_id, customer_name=customer_name,
        vehicle_label=available or "VOYQI loaner (overflow — rental pool)",
    )
    db.add(loaner)
    await db.commit()
    await db.refresh(loaner)
    return loaner


async def return_loaner(db: AsyncSession, tenant_id: str, loaner_id: uuid.UUID) -> Loaner:
    result = await db.execute(select(Loaner).where(Loaner.id == loaner_id, Loaner.tenant_id == tenant_id))
    loaner = result.scalar_one_or_none()
    if loaner is None:
        raise ValueError(f"loaner {loaner_id} not found")
    loaner.status = LoanerStatus.RETURNED
    await db.commit()
    await db.refresh(loaner)
    return loaner


async def evaluate_overdue_loaners(db: AsyncSession, tenant_id: str) -> list[Loaner]:
    settings = get_settings()
    loaners = await list_loaners(db, tenant_id)
    flagged = []
    for loaner in loaners:
        if loaner.status == LoanerStatus.OUT and loaner.days_out >= settings.loaner_extended_days_threshold and not loaner.billing_flag:
            loaner.billing_flag = True
            flagged.append(loaner)
    if flagged:
        await db.commit()
        for loaner in flagged:
            await db.refresh(loaner)
    return flagged
