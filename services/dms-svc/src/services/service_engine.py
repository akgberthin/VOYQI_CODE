"""
DMS-006 — Service & Repair Orders engine.

create_and_dispatch_ro is fully autonomous: opening the RO immediately
assigns a tech and estimates duration from job-description keywords —
never left at an "open, awaiting dispatch" state. A multi-day estimate
auto-chains into a loaner assignment (see loaner_engine.py).
"""
import random
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import RepairOrder, RepairOrderStatus
from src.services import loaner_engine

DURATION_KEYWORDS = [
    (("transmission", "engine", "head gasket"), 3),
    (("recall", "software"), 1),
    (("brake", "suspension", "alignment"), 1),
    (("oil", "tire", "rotation", "wiper", "battery"), 0),
]


def estimate_service_duration(description: str) -> int:
    desc_lower = description.lower()
    for keywords, days in DURATION_KEYWORDS:
        if any(kw in desc_lower for kw in keywords):
            return days
    return 1  # default: assume a full day for anything unrecognized


async def create_and_dispatch_ro(
    db: AsyncSession, tenant_id: str, rooftop_id: uuid.UUID, vin: str, customer_name: str,
    description: str, warranty: bool,
) -> RepairOrder:
    settings = get_settings()
    est_days = estimate_service_duration(description)
    ro = RepairOrder(
        tenant_id=tenant_id, rooftop_id=rooftop_id, vin=vin, customer_name=customer_name,
        description=description, warranty=warranty, tech=f"tech-{random.randint(10, 89)}",
        status=RepairOrderStatus.WARRANTY_CLAIM if warranty else RepairOrderStatus.IN_PROGRESS,
        estimated_duration_days=est_days,
    )
    db.add(ro)
    await db.commit()
    await db.refresh(ro)

    if est_days >= settings.service_loaner_duration_threshold_days:
        await loaner_engine.auto_assign_loaner(db, tenant_id, ro.id, customer_name, est_days)

    return ro


async def get_repair_order(db: AsyncSession, tenant_id: str, ro_id: uuid.UUID) -> RepairOrder | None:
    result = await db.execute(select(RepairOrder).where(RepairOrder.id == ro_id, RepairOrder.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_repair_orders(db: AsyncSession, tenant_id: str) -> list[RepairOrder]:
    result = await db.execute(select(RepairOrder).where(RepairOrder.tenant_id == tenant_id))
    return list(result.scalars().all())


async def complete_ro(db: AsyncSession, tenant_id: str, ro_id: uuid.UUID) -> RepairOrder:
    from sqlalchemy import select
    result = await db.execute(select(RepairOrder).where(RepairOrder.id == ro_id, RepairOrder.tenant_id == tenant_id))
    ro = result.scalar_one_or_none()
    if ro is None:
        raise ValueError(f"repair order {ro_id} not found")
    ro.status = RepairOrderStatus.COMPLETED
    ro.revenue_usd = 0.0 if ro.warranty else round(150 + random.random() * 450, 2)
    await db.commit()
    await db.refresh(ro)
    return ro
