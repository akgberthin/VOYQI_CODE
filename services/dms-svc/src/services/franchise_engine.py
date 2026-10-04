"""
DMS-010 — OEM & Franchise Compliance engine.

evaluate_and_remediate opens a remediation task the instant a
rooftop's status crosses into NON_COMPLIANT — no one has to spot it on
a dashboard first. Guarded by remediation_opened against re-firing on
every re-evaluation.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import FranchiseCompliance, FranchiseStatus


async def create_franchise_record(db: AsyncSession, tenant_id: str, rooftop_id, status: str, score: int, coop_usd: float, rebrand_status: str = "not_applicable", notes: str = "") -> FranchiseCompliance:
    record = FranchiseCompliance(
        tenant_id=tenant_id, rooftop_id=rooftop_id, status=status, score=score,
        coop_usd=coop_usd, rebrand_status=rebrand_status, notes=notes,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def list_franchise_records(db: AsyncSession, tenant_id: str) -> list[FranchiseCompliance]:
    result = await db.execute(select(FranchiseCompliance).where(FranchiseCompliance.tenant_id == tenant_id))
    return list(result.scalars().all())


async def evaluate_and_remediate(db: AsyncSession, tenant_id: str) -> list[FranchiseCompliance]:
    records = await list_franchise_records(db, tenant_id)
    opened = []
    for record in records:
        if record.status == FranchiseStatus.NON_COMPLIANT and not record.remediation_opened:
            record.remediation_opened = True
            opened.append(record)
    if opened:
        await db.commit()
        for record in opened:
            await db.refresh(record)
    return opened
