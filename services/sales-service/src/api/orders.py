import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import OrderStatus
from src.services import sales_order_engine
from src.services.sales_order_engine import InvalidTransitionError

router = APIRouter(prefix="/api/v1/sales/orders", tags=["orders"])


class OrderIn(BaseModel):
    order_number: str
    customer_name: str
    total_amount_usd: float
    source_deal_id: str | None = None


class OrderOut(BaseModel):
    id: str
    order_number: str
    customer_name: str
    source_deal_id: str | None
    total_amount_usd: float
    status: OrderStatus


class TransitionIn(BaseModel):
    to_status: OrderStatus
    note: str | None = None


class StatusEventOut(BaseModel):
    from_status: OrderStatus
    to_status: OrderStatus
    note: str | None
    created_at: datetime


def _to_out(o) -> OrderOut:
    return OrderOut(
        id=str(o.id), order_number=o.order_number, customer_name=o.customer_name,
        source_deal_id=o.source_deal_id, total_amount_usd=o.total_amount_usd, status=o.status,
    )


@router.post("", response_model=OrderOut, status_code=201)
async def create_order(
    payload: OrderIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> OrderOut:
    order = await sales_order_engine.create_order(
        db, tenant_id, payload.order_number, payload.customer_name, payload.total_amount_usd, payload.source_deal_id
    )
    return _to_out(order)


@router.get("", response_model=list[OrderOut])
async def list_orders(
    status: OrderStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> list[OrderOut]:
    orders = await sales_order_engine.list_orders(db, tenant_id, status)
    return [_to_out(o) for o in orders]


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> OrderOut:
    order = await sales_order_engine.get_order(db, tenant_id, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return _to_out(order)


@router.post("/{order_id}/transition", response_model=OrderOut)
async def transition_order(
    order_id: uuid.UUID, payload: TransitionIn,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
) -> OrderOut:
    try:
        order = await sales_order_engine.transition_order(db, tenant_id, order_id, payload.to_status, payload.note)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return _to_out(order)


@router.get("/{order_id}/history", response_model=list[StatusEventOut])
async def order_history(
    order_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> list[StatusEventOut]:
    events = await sales_order_engine.list_status_history(db, tenant_id, order_id)
    return [
        StatusEventOut(from_status=e.from_status, to_status=e.to_status, note=e.note, created_at=e.created_at)
        for e in events
    ]
