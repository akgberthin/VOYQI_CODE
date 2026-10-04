"""
MOB-002 Subscription Plan Manager engine.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ClassEquivalency, MobilityClass, Plan

INVITATION_ONLY_CLASSES = {MobilityClass.LUXURY_PLUS, MobilityClass.PERFORMANCE_PLUS}


async def create_plan(
    db: AsyncSession, tenant_id: str, mobility_class: MobilityClass, market: str,
    monthly_price_usd: float, entitlements: dict | None = None,
) -> Plan:
    plan = Plan(
        tenant_id=tenant_id, mobility_class=mobility_class, market=market, monthly_price_usd=monthly_price_usd,
        entitlements=entitlements or {}, is_invitation_only=mobility_class in INVITATION_ONLY_CLASSES,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


async def list_plans(db: AsyncSession, tenant_id: str, market: str | None = None) -> list[Plan]:
    stmt = select(Plan).where(Plan.tenant_id == tenant_id)
    if market:
        stmt = stmt.where(Plan.market == market)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_plan(db: AsyncSession, tenant_id: str, plan_id: uuid.UUID) -> Plan | None:
    result = await db.execute(select(Plan).where(Plan.id == plan_id, Plan.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def create_class_equivalency(
    db: AsyncSession, tenant_id: str, source_class: MobilityClass, destination_market: str,
    equivalent_class: MobilityClass, notes: str = "",
) -> ClassEquivalency:
    equivalency = ClassEquivalency(
        tenant_id=tenant_id, source_class=source_class, destination_market=destination_market,
        equivalent_class=equivalent_class, notes=notes,
    )
    db.add(equivalency)
    await db.commit()
    await db.refresh(equivalency)
    return equivalency


async def resolve_equivalent_class(
    db: AsyncSession, tenant_id: str, source_class: MobilityClass, destination_market: str
) -> MobilityClass:
    """Falls back to the source class unchanged if no equivalency rule
    is defined for that market — continuity should never fail outright
    just because a rule hasn't been configured yet."""
    result = await db.execute(
        select(ClassEquivalency).where(
            ClassEquivalency.tenant_id == tenant_id, ClassEquivalency.source_class == source_class,
            ClassEquivalency.destination_market == destination_market,
        )
    )
    equivalency = result.scalar_one_or_none()
    return equivalency.equivalent_class if equivalency else source_class
