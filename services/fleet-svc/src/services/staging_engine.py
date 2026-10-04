"""
FLT-003 Staging & Hub Allocation Board engine.

auto_dispatch_transfer is the Fleet Intelligence Agent's autonomous
path: it creates AND dispatches a transfer order in one atomic action
— no separate human confirmation step. dispatch_transfer remains for
a human operator to dispatch a transfer that was created some other
way (e.g. a manually created PROPOSED order), but the agent itself
never stops at PROPOSED.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Hub, TransferOrder, TransferOrderStatus, Vehicle, VehicleStatus


async def create_hub(db: AsyncSession, tenant_id: str, name: str, region: str = "") -> Hub:
    hub = Hub(tenant_id=tenant_id, name=name, region=region)
    db.add(hub)
    await db.commit()
    await db.refresh(hub)
    return hub


async def get_hub_supply(db: AsyncSession, tenant_id: str, hub_id: uuid.UUID) -> dict[str, int]:
    """Current available (STAGED) vehicle count per class at a hub —
    the 'supply' half of the supply/demand heatmap."""
    result = await db.execute(
        select(Vehicle.vehicle_class, func.count(Vehicle.id)).where(
            Vehicle.tenant_id == tenant_id, Vehicle.current_hub_id == hub_id, Vehicle.status == VehicleStatus.STAGED,
        ).group_by(Vehicle.vehicle_class)
    )
    return {row[0].value: row[1] for row in result.all()}


async def propose_transfer(
    db: AsyncSession,
    tenant_id: str,
    vehicle_id: uuid.UUID,
    to_hub_id: uuid.UUID,
    reason: str,
    ai_initiated: bool = False,
    generated_by_agent_key: str | None = None,
) -> TransferOrder:
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        raise ValueError(f"vehicle {vehicle_id} not found")

    order = TransferOrder(
        tenant_id=tenant_id, vehicle_id=vehicle_id, from_hub_id=vehicle.current_hub_id, to_hub_id=to_hub_id,
        reason=reason, ai_initiated=ai_initiated, generated_by_agent_key=generated_by_agent_key,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


async def auto_dispatch_transfer(
    db: AsyncSession,
    tenant_id: str,
    vehicle_id: uuid.UUID,
    to_hub_id: uuid.UUID,
    reason: str,
    agent_key: str,
) -> TransferOrder:
    """The Fleet Intelligence Agent's fully autonomous staging action:
    creates the transfer order and dispatches it in the same atomic
    step, moving the vehicle to IN_TRANSIT immediately. No human
    confirmation gate — the decision log is the audit trail."""
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        raise ValueError(f"vehicle {vehicle_id} not found")

    order = TransferOrder(
        tenant_id=tenant_id, vehicle_id=vehicle_id, from_hub_id=vehicle.current_hub_id, to_hub_id=to_hub_id,
        reason=reason, ai_initiated=True, generated_by_agent_key=agent_key,
        status=TransferOrderStatus.DISPATCHED, dispatched_by=agent_key, driver_id="auto-dispatch-logistics-queue",
        dispatched_at=datetime.now(timezone.utc),
    )
    db.add(order)
    vehicle.status = VehicleStatus.IN_TRANSIT
    await db.commit()
    await db.refresh(order)
    return order


async def dispatch_transfer(
    db: AsyncSession, tenant_id: str, transfer_id: uuid.UUID, dispatched_by: str, driver_id: str
) -> TransferOrder | None:
    result = await db.execute(select(TransferOrder).where(TransferOrder.id == transfer_id, TransferOrder.tenant_id == tenant_id))
    order = result.scalar_one_or_none()
    if order is None or order.status != TransferOrderStatus.PROPOSED:
        return None

    order.status = TransferOrderStatus.DISPATCHED
    order.dispatched_by = dispatched_by
    order.driver_id = driver_id
    order.dispatched_at = datetime.now(timezone.utc)

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == order.vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        vehicle.status = VehicleStatus.IN_TRANSIT

    await db.commit()
    await db.refresh(order)
    return order


async def complete_transfer(db: AsyncSession, tenant_id: str, transfer_id: uuid.UUID) -> TransferOrder | None:
    result = await db.execute(select(TransferOrder).where(TransferOrder.id == transfer_id, TransferOrder.tenant_id == tenant_id))
    order = result.scalar_one_or_none()
    if order is None or order.status != TransferOrderStatus.DISPATCHED:
        return None

    order.status = TransferOrderStatus.COMPLETED
    order.completed_at = datetime.now(timezone.utc)

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == order.vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        vehicle.current_hub_id = order.to_hub_id
        vehicle.status = VehicleStatus.STAGED

    await db.commit()
    await db.refresh(order)
    return order


async def cancel_transfer(db: AsyncSession, tenant_id: str, transfer_id: uuid.UUID) -> TransferOrder | None:
    result = await db.execute(select(TransferOrder).where(TransferOrder.id == transfer_id, TransferOrder.tenant_id == tenant_id))
    order = result.scalar_one_or_none()
    if order is None or order.status not in (TransferOrderStatus.PROPOSED, TransferOrderStatus.DISPATCHED):
        return None
    order.status = TransferOrderStatus.CANCELLED
    await db.commit()
    await db.refresh(order)
    return order


async def list_transfers(
    db: AsyncSession, tenant_id: str, status: TransferOrderStatus | None = None
) -> list[TransferOrder]:
    stmt = select(TransferOrder).where(TransferOrder.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(TransferOrder.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())
