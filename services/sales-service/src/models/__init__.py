import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class OrderStatus(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


# Legal transitions — enforced in sales_order_engine.py, not left to
# convention. A sales order's lifecycle only ever moves forward or to
# cancelled; it never reopens once fulfilled or cancelled, the same
# structural rule rental-svc applies to a reservation's rate.
ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.DRAFT: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.FULFILLED, OrderStatus.CANCELLED},
    OrderStatus.FULFILLED: set(),
    OrderStatus.CANCELLED: set(),
}


class SalesOrder(Base):
    """A sales transaction moving through execution — this is Layer 1
    core execution (the actual order/fulfillment record), distinct
    from crm-svc's relationship/pipeline tracking (leads, deals) and
    from dms-svc's dealership-specific deal desk. A sales order can
    originate from a crm-svc deal (source_deal_id) but is its own
    record once it exists."""

    __tablename__ = "sales_orders"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    order_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    customer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_deal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), nullable=False, default=OrderStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OrderStatusEvent(Base):
    """Immutable history of every status transition — append-only, so
    "what happened and when" survives even though SalesOrder.status
    itself is a mutable current-state field."""

    __tablename__ = "order_status_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("sales_orders.id"), nullable=False, index=True)
    from_status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), nullable=False)
    to_status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
