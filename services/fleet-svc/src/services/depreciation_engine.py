"""
FLT-006 Depreciation & Asset Accounting engine — straight-line
depreciation only, plain arithmetic.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    AssetValuation,
    DepreciationSchedule,
    ValuationAnomalyAssessment,
    ValuationAnomalyLevel,
)


async def create_schedule(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, acquisition_cost_usd: float,
    salvage_value_usd: float, useful_life_months: int,
) -> DepreciationSchedule:
    schedule = DepreciationSchedule(
        tenant_id=tenant_id, vehicle_id=vehicle_id, acquisition_cost_usd=acquisition_cost_usd,
        salvage_value_usd=salvage_value_usd, useful_life_months=useful_life_months,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


def compute_book_value(schedule: DepreciationSchedule, months_elapsed: int) -> float:
    monthly_depreciation = (schedule.acquisition_cost_usd - schedule.salvage_value_usd) / schedule.useful_life_months
    months_elapsed = min(months_elapsed, schedule.useful_life_months)
    book_value = schedule.acquisition_cost_usd - (monthly_depreciation * months_elapsed)
    return max(book_value, schedule.salvage_value_usd)


async def get_current_book_value(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, as_of: datetime | None = None) -> dict | None:
    result = await db.execute(select(DepreciationSchedule).where(DepreciationSchedule.tenant_id == tenant_id, DepreciationSchedule.vehicle_id == vehicle_id))
    schedule = result.scalar_one_or_none()
    if schedule is None:
        return None

    created_at = schedule.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    as_of = as_of or datetime.now(timezone.utc)
    months_elapsed = max(0, int((as_of - created_at).days / 30))

    book_value = compute_book_value(schedule, months_elapsed)
    return {"vehicle_id": str(vehicle_id), "months_elapsed": months_elapsed, "book_value_usd": book_value}


async def record_market_valuation(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, market_value_estimate_usd: float, valued_by: str
) -> AssetValuation:
    valuation = AssetValuation(
        tenant_id=tenant_id, vehicle_id=vehicle_id, market_value_estimate_usd=market_value_estimate_usd, valued_by=valued_by,
    )
    db.add(valuation)
    await db.commit()
    await db.refresh(valuation)
    return valuation


async def get_asset_valuation(db: AsyncSession, tenant_id: str, asset_valuation_id: uuid.UUID) -> AssetValuation | None:
    result = await db.execute(select(AssetValuation).where(AssetValuation.id == asset_valuation_id, AssetValuation.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def record_valuation_anomaly(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, asset_valuation_id: uuid.UUID, deviation_pct: float, assessment: ValuationAnomalyLevel, reasoning: str,
) -> ValuationAnomalyAssessment:
    record = ValuationAnomalyAssessment(
        tenant_id=tenant_id, vehicle_id=vehicle_id, asset_valuation_id=asset_valuation_id, deviation_pct=deviation_pct, assessment=assessment, reasoning=reasoning,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_valuation_anomaly(db: AsyncSession, tenant_id: str, asset_valuation_id: uuid.UUID) -> ValuationAnomalyAssessment | None:
    result = await db.execute(
        select(ValuationAnomalyAssessment).where(ValuationAnomalyAssessment.tenant_id == tenant_id, ValuationAnomalyAssessment.asset_valuation_id == asset_valuation_id)
        .order_by(ValuationAnomalyAssessment.computed_at.desc())
    )
    return result.scalars().first()
