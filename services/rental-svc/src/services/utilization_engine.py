"""
Utilization engine — the supply-side signal: how full a hub is right
now. Fully autonomous: computing and recording a snapshot is not an
access-control decision.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import RentalVehicle, UtilizationSnapshot, VehicleStatus


async def compute_utilization(db: AsyncSession, tenant_id: str, hub_id: str, rental_class: str) -> dict:
    """The real, current booked/available ratio — computed from
    actual vehicle status, not estimated."""
    result = await db.execute(select(RentalVehicle).where(RentalVehicle.tenant_id == tenant_id, RentalVehicle.hub_id == hub_id, RentalVehicle.rental_class == rental_class))
    vehicles = list(result.scalars().all())
    total = len(vehicles)
    booked = len([v for v in vehicles if v.status == VehicleStatus.RENTED])
    utilization_pct = round((booked / total) * 100, 1) if total else 0.0
    return {"hub_id": hub_id, "rental_class": rental_class, "booked_count": booked, "available_count": total - booked, "total_count": total, "utilization_pct": utilization_pct}


async def record_snapshot(db: AsyncSession, tenant_id: str, hub_id: str, rental_class: str) -> UtilizationSnapshot:
    computed = await compute_utilization(db, tenant_id, hub_id, rental_class)
    snapshot = UtilizationSnapshot(
        tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class,
        booked_count=computed["booked_count"], total_count=computed["total_count"], utilization_pct=computed["utilization_pct"],
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


async def list_snapshots(db: AsyncSession, tenant_id: str, hub_id: str | None = None, rental_class: str | None = None) -> list[UtilizationSnapshot]:
    query = select(UtilizationSnapshot).where(UtilizationSnapshot.tenant_id == tenant_id)
    if hub_id:
        query = query.where(UtilizationSnapshot.hub_id == hub_id)
    if rental_class:
        query = query.where(UtilizationSnapshot.rental_class == rental_class)
    result = await db.execute(query.order_by(UtilizationSnapshot.snapshot_at))
    return list(result.scalars().all())
