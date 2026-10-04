import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.kafka_events import publish_event
from src.models import ALLOWED_TRANSITIONS, OrderStatus, OrderStatusEvent, SalesOrder


class InvalidTransitionError(Exception):
    pass


async def create_order(
    db: AsyncSession,
    tenant_id: str,
    order_number: str,
    customer_name: str,
    total_amount_usd: float,
    source_deal_id: str | None = None,
) -> SalesOrder:
    order = SalesOrder(
        tenant_id=tenant_id, order_number=order_number, customer_name=customer_name,
        total_amount_usd=total_amount_usd, source_deal_id=source_deal_id,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    await publish_event("sales.order_created", {"tenant_id": tenant_id, "order_id": str(order.id)})
    return order


async def get_order(db: AsyncSession, tenant_id: str, order_id: uuid.UUID) -> SalesOrder | None:
    result = await db.execute(select(SalesOrder).where(SalesOrder.tenant_id == tenant_id, SalesOrder.id == order_id))
    return result.scalar_one_or_none()


async def list_orders(db: AsyncSession, tenant_id: str, status: OrderStatus | None = None) -> list[SalesOrder]:
    stmt = select(SalesOrder).where(SalesOrder.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(SalesOrder.status == status)
    result = await db.execute(stmt.order_by(SalesOrder.created_at.desc()))
    return list(result.scalars().all())


async def transition_order(
    db: AsyncSession, tenant_id: str, order_id: uuid.UUID, to_status: OrderStatus, note: str | None = None
) -> SalesOrder | None:
    order = await get_order(db, tenant_id, order_id)
    if order is None:
        return None

    if to_status not in ALLOWED_TRANSITIONS[order.status]:
        raise InvalidTransitionError(
            f"cannot move a {order.status.value} order to {to_status.value}"
        )

    from_status = order.status
    order.status = to_status
    db.add(OrderStatusEvent(tenant_id=tenant_id, order_id=order.id, from_status=from_status, to_status=to_status, note=note))
    await db.commit()
    await db.refresh(order)

    await publish_event(
        "sales.order_status_changed",
        {"tenant_id": tenant_id, "order_id": str(order.id), "from_status": from_status.value, "to_status": to_status.value},
    )
    return order


async def list_status_history(db: AsyncSession, tenant_id: str, order_id: uuid.UUID) -> list[OrderStatusEvent]:
    result = await db.execute(
        select(OrderStatusEvent)
        .where(OrderStatusEvent.tenant_id == tenant_id, OrderStatusEvent.order_id == order_id)
        .order_by(OrderStatusEvent.created_at.asc())
    )
    return list(result.scalars().all())
