"""
DMS-005 — Trade-in & Appraisal Console engine.

apply_valuation_and_offer is the terminal action the Pricing
Intelligence Agent calls after computing a valuation — it writes
valuation_usd AND offer_usd in the same call, no separate acceptance
step. security_hold is the structural block: a stolen-vehicle registry
match routes here directly and has no code path to OFFERED, mirroring
lending_engine.py's OFAC design exactly. This engine never computes
the security check itself — that's dms-svc's own deterministic
registry lookup, not an AI judgment call, so it's checked before this
engine is ever reached (see the API layer).
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import Appraisal, AppraisalStatus


async def find_or_create_appraisal(db: AsyncSession, tenant_id: str, vin: str, mileage: int) -> Appraisal:
    result = await db.execute(
        select(Appraisal).where(
            Appraisal.vin == vin, Appraisal.tenant_id == tenant_id,
            Appraisal.status.in_([AppraisalStatus.PENDING, AppraisalStatus.OFFERED]),
        )
    )
    existing = result.scalars().first()
    if existing:
        existing.mileage = mileage
        await db.commit()
        await db.refresh(existing)
        return existing

    appraisal = Appraisal(tenant_id=tenant_id, vin=vin, mileage=mileage)
    db.add(appraisal)
    await db.commit()
    await db.refresh(appraisal)
    return appraisal


async def get_appraisal(db: AsyncSession, tenant_id: str, appraisal_id: uuid.UUID) -> Appraisal | None:
    result = await db.execute(select(Appraisal).where(Appraisal.id == appraisal_id, Appraisal.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_appraisals(db: AsyncSession, tenant_id: str) -> list[Appraisal]:
    result = await db.execute(select(Appraisal).where(Appraisal.tenant_id == tenant_id))
    return list(result.scalars().all())


async def set_security_hold(db: AsyncSession, tenant_id: str, appraisal_id: uuid.UUID) -> Appraisal:
    """No override exists. Once set, this appraisal has no further
    code path to OFFERED or ACCEPTED."""
    appraisal = await get_appraisal(db, tenant_id, appraisal_id)
    if appraisal is None:
        raise ValueError(f"appraisal {appraisal_id} not found")
    appraisal.status = AppraisalStatus.SECURITY_HOLD
    appraisal.valuation_usd = None
    appraisal.offer_usd = None
    await db.commit()
    await db.refresh(appraisal)
    return appraisal


async def apply_valuation_and_offer(db: AsyncSession, tenant_id: str, appraisal_id: uuid.UUID, valuation_usd: float) -> Appraisal:
    """Fully autonomous: valuation and offer happen in the same call.
    Wholesale offer margin is a fixed, auditable percentage of
    valuation (see config.trade_offer_pct_of_valuation)."""
    settings = get_settings()
    appraisal = await get_appraisal(db, tenant_id, appraisal_id)
    if appraisal is None:
        raise ValueError(f"appraisal {appraisal_id} not found")
    if appraisal.status == AppraisalStatus.SECURITY_HOLD:
        raise ValueError("cannot offer on an appraisal under security hold")
    appraisal.valuation_usd = valuation_usd
    appraisal.offer_usd = round(valuation_usd * settings.trade_offer_pct_of_valuation, 2)
    appraisal.status = AppraisalStatus.OFFERED
    await db.commit()
    await db.refresh(appraisal)
    return appraisal


async def accept_appraisal(db: AsyncSession, tenant_id: str, appraisal_id: uuid.UUID, rooftop_id: uuid.UUID) -> Appraisal:
    """Accepting/declining the offer is the customer's own decision,
    not something the agent should make on their behalf — this
    records that real-world outcome, it doesn't compute one."""
    from src.models import Vehicle, VehicleCondition, VehicleStatus

    appraisal = await get_appraisal(db, tenant_id, appraisal_id)
    if appraisal is None:
        raise ValueError(f"appraisal {appraisal_id} not found")
    appraisal.status = AppraisalStatus.ACCEPTED

    vehicle = Vehicle(
        tenant_id=tenant_id, rooftop_id=rooftop_id, vin=appraisal.vin,
        description=f"Trade-in acquisition ({appraisal.vin})", condition=VehicleCondition.USED,
        price_usd=round(appraisal.offer_usd * 1.15, 2), cost_usd=appraisal.offer_usd,
        days_in_inventory=0, status=VehicleStatus.IN_RECON,
    )
    db.add(vehicle)
    await db.commit()
    await db.refresh(appraisal)
    return appraisal


async def decline_appraisal(db: AsyncSession, tenant_id: str, appraisal_id: uuid.UUID) -> Appraisal:
    appraisal = await get_appraisal(db, tenant_id, appraisal_id)
    if appraisal is None:
        raise ValueError(f"appraisal {appraisal_id} not found")
    appraisal.status = AppraisalStatus.DECLINED
    await db.commit()
    await db.refresh(appraisal)
    return appraisal
