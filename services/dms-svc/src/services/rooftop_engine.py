"""
DMS-001 — Rooftop Directory & Entity Mapping engine.

Cross-rooftop inventory transfer is never implemented here: each
rooftop holds a single-brand OEM franchise, so a vehicle can only ever
be sold at the one rooftop licensed for its brand — there's no
legitimate rebalancing target in this data model. What IS automated:
license renewal filing, the instant a rooftop is within the renewal
window of expiry.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import Rooftop


async def create_rooftop(
    db: AsyncSession, tenant_id: str, name: str, holding_company: str, oem_brand: str,
    license_number: str, license_expiry: datetime,
) -> Rooftop:
    rooftop = Rooftop(
        tenant_id=tenant_id, name=name, holding_company=holding_company, oem_brand=oem_brand,
        license_number=license_number, license_expiry=license_expiry,
    )
    db.add(rooftop)
    await db.commit()
    await db.refresh(rooftop)
    return rooftop


async def list_rooftops(db: AsyncSession, tenant_id: str) -> list[Rooftop]:
    result = await db.execute(select(Rooftop).where(Rooftop.tenant_id == tenant_id))
    return list(result.scalars().all())


async def get_rooftop(db: AsyncSession, tenant_id: str, rooftop_id: uuid.UUID) -> Rooftop | None:
    result = await db.execute(select(Rooftop).where(Rooftop.id == rooftop_id, Rooftop.tenant_id == tenant_id))
    return result.scalar_one_or_none()


def days_until_expiry(rooftop: Rooftop) -> int:
    expiry = rooftop.license_expiry
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return (expiry - datetime.now(timezone.utc)).days


async def evaluate_and_file_renewals(db: AsyncSession, tenant_id: str) -> list[Rooftop]:
    """Fully autonomous: any rooftop within the renewal window that
    hasn't already been filed gets filed now, in the same call — no
    human has to notice the expiring license first. Returns the
    rooftops that were just filed (empty list if none were due)."""
    settings = get_settings()
    rooftops = await list_rooftops(db, tenant_id)
    filed = []
    for rooftop in rooftops:
        if rooftop.renewal_filed:
            continue
        if days_until_expiry(rooftop) < settings.license_renewal_window_days:
            rooftop.renewal_filed = True
            filed.append(rooftop)
    if filed:
        await db.commit()
        for rooftop in filed:
            await db.refresh(rooftop)
    return filed
