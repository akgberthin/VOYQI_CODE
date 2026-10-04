"""
FLT-013 Fleet Utilization & Total-Cost-of-Ownership Analytics.

Every commercial fleet platform researched (Samsara, Motive, Geotab)
markets a unified analytics dashboard as a core differentiator. This
closes that gap using facts fleet-svc already owns — no new tracking,
just honest arithmetic across depreciation, maintenance, fuel, and
charging data that previously lived in separate modules with no
aggregation.

Scope, stated plainly: TCO here means acquisition depreciation-to-date
plus maintenance plus fuel/energy cost. It deliberately excludes
floorplan financing interest allocation — apportioning a facility-level
interest rate to one vehicle would require assumptions this module
has no business inventing. Availability rate means the share of time
since acquisition NOT spent in maintenance downtime — it is not the
same as rental utilization (the share of time actually rented), which
lives in rental-svc's own transaction data, not here. Calling it
"utilization" without that distinction would overstate what fleet-svc
can actually see.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    AssetValuation,
    DepreciationSchedule,
    FleetOptimizationAssessment,
    FleetOptimizationRecommendation,
    Vehicle,
    WorkOrder,
    WorkOrderStatus,
)
from src.services import (
    depreciation_engine,
    ev_charging_engine,
    fuel_engine,
    remarketing_engine,
)


async def _total_maintenance_cost(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> float:
    result = await db.execute(
        select(WorkOrder).where(WorkOrder.tenant_id == tenant_id, WorkOrder.vehicle_id == vehicle_id, WorkOrder.status == WorkOrderStatus.COMPLETED)
    )
    orders = list(result.scalars().all())
    return round(sum(o.cost_usd for o in orders), 2)


async def _total_maintenance_downtime_hours(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> float:
    result = await db.execute(select(WorkOrder).where(WorkOrder.tenant_id == tenant_id, WorkOrder.vehicle_id == vehicle_id))
    orders = list(result.scalars().all())
    total_hours = 0.0
    for o in orders:
        end = o.downtime_end or datetime.now(timezone.utc)
        start = o.downtime_start if o.downtime_start.tzinfo else o.downtime_start.replace(tzinfo=timezone.utc)
        end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        total_hours += max(0.0, (end - start).total_seconds() / 3600)
    return round(total_hours, 2)


async def get_vehicle_tco(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> dict:
    schedule_result = await db.execute(select(DepreciationSchedule).where(DepreciationSchedule.tenant_id == tenant_id, DepreciationSchedule.vehicle_id == vehicle_id))
    schedule = schedule_result.scalar_one_or_none()

    depreciation_to_date = None
    if schedule:
        book_value_info = await depreciation_engine.get_current_book_value(db, tenant_id, vehicle_id)
        if book_value_info:
            depreciation_to_date = round(schedule.acquisition_cost_usd - book_value_info["book_value_usd"], 2)

    maintenance_cost = await _total_maintenance_cost(db, tenant_id, vehicle_id)
    fuel_summary = await fuel_engine.get_fuel_summary(db, tenant_id, vehicle_id)
    charging_summary = await ev_charging_engine.get_charging_cost_summary(db, tenant_id, vehicle_id)
    energy_cost = fuel_summary["total_cost_usd"] + charging_summary["total_cost_usd"]

    total_tco = None
    if depreciation_to_date is not None:
        total_tco = round(depreciation_to_date + maintenance_cost + energy_cost, 2)

    return {
        "vehicle_id": str(vehicle_id),
        "depreciation_to_date_usd": depreciation_to_date,
        "maintenance_cost_usd": maintenance_cost,
        "energy_cost_usd": round(energy_cost, 2),
        "total_tco_usd": total_tco,
    }


async def get_vehicle_availability(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, as_of: datetime | None = None) -> dict:
    """Deliberately named 'availability', not 'utilization' — see
    module docstring. Measures the inverse of maintenance downtime
    since acquisition, using only facts fleet-svc actually owns."""
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        return {"vehicle_id": str(vehicle_id), "error": "vehicle not found"}

    acquired_at = vehicle.acquired_at if vehicle.acquired_at.tzinfo else vehicle.acquired_at.replace(tzinfo=timezone.utc)
    as_of = as_of or datetime.now(timezone.utc)
    total_hours = max(0.0, (as_of - acquired_at).total_seconds() / 3600)
    downtime_hours = await _total_maintenance_downtime_hours(db, tenant_id, vehicle_id)
    availability_pct = round((1 - (downtime_hours / total_hours)) * 100, 1) if total_hours > 0 else None

    return {
        "vehicle_id": str(vehicle_id),
        "total_hours_since_acquisition": round(total_hours, 1),
        "maintenance_downtime_hours": downtime_hours,
        "availability_pct": availability_pct,
    }


async def get_fleet_summary(db: AsyncSession, tenant_id: str) -> dict:
    """Fleet-wide rollup by status and class — the count-based view
    that already existed per-module, now in one place."""
    result = await db.execute(select(Vehicle).where(Vehicle.tenant_id == tenant_id))
    vehicles = list(result.scalars().all())
    by_status: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for v in vehicles:
        by_status[v.status.value] = by_status.get(v.status.value, 0) + 1
        by_class[v.vehicle_class.value] = by_class.get(v.vehicle_class.value, 0) + 1
    return {"total_vehicles": len(vehicles), "by_status": by_status, "by_class": by_class}


async def gather_fleet_optimization_signals(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> dict:
    """Pulls together the real facts from FLT-006/007/013 the
    optimization recommendation needs — this is plain data gathering,
    no judgment; the Fleet Intelligence Agent does the actual
    weighing."""
    tco = await get_vehicle_tco(db, tenant_id, vehicle_id)
    availability = await get_vehicle_availability(db, tenant_id, vehicle_id)
    eligibility = await remarketing_engine.evaluate_disposition_eligibility(db, tenant_id, vehicle_id)

    latest_valuation_flagged = False
    valuations_result = await db.execute(
        select(AssetValuation).where(AssetValuation.tenant_id == tenant_id, AssetValuation.vehicle_id == vehicle_id).order_by(AssetValuation.valued_at.desc())
    )
    latest_valuation = valuations_result.scalars().first()
    if latest_valuation:
        anomaly = await depreciation_engine.get_valuation_anomaly(db, tenant_id, latest_valuation.id)
        if anomaly and anomaly.assessment.value == "flagged":
            latest_valuation_flagged = True

    return {
        "vehicle_id": str(vehicle_id),
        "total_tco_usd": tco["total_tco_usd"],
        "availability_pct": availability.get("availability_pct"),
        "is_disposition_eligible": eligibility["eligible"] if eligibility else False,
        "valuation_flagged": latest_valuation_flagged,
    }


async def record_fleet_optimization(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, recommendation: FleetOptimizationRecommendation, reasoning: str) -> FleetOptimizationAssessment:
    assessment = FleetOptimizationAssessment(tenant_id=tenant_id, vehicle_id=vehicle_id, recommendation=recommendation, reasoning=reasoning)
    db.add(assessment)
    await db.commit()
    await db.refresh(assessment)
    return assessment


async def get_latest_fleet_optimization(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> FleetOptimizationAssessment | None:
    result = await db.execute(
        select(FleetOptimizationAssessment).where(FleetOptimizationAssessment.tenant_id == tenant_id, FleetOptimizationAssessment.vehicle_id == vehicle_id)
        .order_by(FleetOptimizationAssessment.computed_at.desc())
    )
    return result.scalars().first()
