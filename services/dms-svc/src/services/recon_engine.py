"""
DMS-006 (extension) — Recon Workflow engine.

add_work_item auto-assigns to a shop resource by category — never
"unassigned" waiting on a dispatcher. complete_work_item is a
technician reporting real physical completion, so it stays a direct,
ungated action; but the moment every item on an order is done, the
order auto-advances to front-line-ready in the same call, no manager
click required.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    ReconOrder,
    ReconOrderStatus,
    ReconWorkItem,
    ReconWorkItemStatus,
    Vehicle,
    VehicleStatus,
)

ASSIGNEE_BY_CATEGORY = {
    "mechanical": "tech-14",
    "detail": "detail-shop",
    "body": "body-shop-1",
    "tires": "tire-bay",
    "other": "shop-foreman",
}


async def create_recon_order(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, arrived_days_ago: int = 0) -> ReconOrder:
    order = ReconOrder(tenant_id=tenant_id, vehicle_id=vehicle_id, arrived_days_ago=arrived_days_ago)
    db.add(order)

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        vehicle.status = VehicleStatus.IN_RECON

    await db.commit()
    await db.refresh(order)
    return order


async def get_recon_order(db: AsyncSession, tenant_id: str, order_id: uuid.UUID) -> ReconOrder | None:
    result = await db.execute(select(ReconOrder).where(ReconOrder.id == order_id, ReconOrder.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_recon_orders(db: AsyncSession, tenant_id: str) -> list[ReconOrder]:
    result = await db.execute(select(ReconOrder).where(ReconOrder.tenant_id == tenant_id))
    return list(result.scalars().all())


async def get_work_items(db: AsyncSession, tenant_id: str, order_id: uuid.UUID) -> list[ReconWorkItem]:
    result = await db.execute(select(ReconWorkItem).where(ReconWorkItem.recon_order_id == order_id, ReconWorkItem.tenant_id == tenant_id))
    return list(result.scalars().all())


async def add_work_item(db: AsyncSession, tenant_id: str, order_id: uuid.UUID, category: str, description: str, cost_usd: float) -> ReconWorkItem:
    """Fully autonomous assignment by category — never 'unassigned'."""
    order = await get_recon_order(db, tenant_id, order_id)
    if order is None:
        raise ValueError(f"recon order {order_id} not found")

    item = ReconWorkItem(
        tenant_id=tenant_id, recon_order_id=order_id, category=category, description=description,
        cost_usd=cost_usd, assigned_to=ASSIGNEE_BY_CATEGORY.get(category, "shop-foreman"),
    )
    db.add(item)
    if order.status == ReconOrderStatus.AWAITING_INSPECTION:
        order.status = ReconOrderStatus.IN_PROGRESS
    await db.commit()
    await db.refresh(item)
    return item


async def complete_work_item(db: AsyncSession, tenant_id: str, item_id: uuid.UUID) -> tuple[ReconWorkItem, ReconOrder]:
    """Marks the item done (a technician's real-world report), then
    checks whether the whole order can auto-advance to front-line
    ready — that check-and-advance IS the autonomous business
    decision; marking one item done is not."""
    result = await db.execute(select(ReconWorkItem).where(ReconWorkItem.id == item_id, ReconWorkItem.tenant_id == tenant_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise ValueError(f"work item {item_id} not found")
    item.status = ReconWorkItemStatus.COMPLETED
    await db.commit()
    await db.refresh(item)

    order = await get_recon_order(db, tenant_id, item.recon_order_id)
    all_items = await get_work_items(db, tenant_id, order.id)
    if all_items and all(i.status == ReconWorkItemStatus.COMPLETED for i in all_items):
        await auto_advance_to_front_line_ready(db, tenant_id, order.id)
        await db.refresh(order)
    return item, order


async def auto_advance_to_front_line_ready(db: AsyncSession, tenant_id: str, order_id: uuid.UUID) -> ReconOrder:
    order = await get_recon_order(db, tenant_id, order_id)
    if order is None:
        raise ValueError(f"recon order {order_id} not found")
    order.status = ReconOrderStatus.COMPLETED
    order.turnaround_days = order.arrived_days_ago

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == order.vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        vehicle.status = VehicleStatus.IN_STOCK
        vehicle.days_in_inventory = 0

    await db.commit()
    await db.refresh(order)
    return order
