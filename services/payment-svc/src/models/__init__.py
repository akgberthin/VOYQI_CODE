import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import GUID, Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderMode(str, enum.Enum):
    """Honest accounting of whether a charge/refund actually touched a
    real payment processor or was simulated because no processor
    credentials are configured in this environment."""

    REAL_PROVIDER = "real_provider"
    SANDBOX = "sandbox"


class PaymentMethodType(str, enum.Enum):
    CARD = "card"
    BANK_ACCOUNT = "bank_account"


class PaymentMethodStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REMOVED = "removed"


class ChargeStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class RefundStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PaymentMethod(Base):
    """Only ever stores a processor token and display metadata (last4,
    brand, expiry) — raw card/bank numbers never touch this database or
    this service's memory. provider_payment_method_id is opaque to us;
    it means something only to the processor that issued it."""

    __tablename__ = "payment_methods"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, index=True)
    provider: Mapped[str] = mapped_column(String(50), default="stripe")
    provider_mode: Mapped[str] = mapped_column(String(20))
    provider_payment_method_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    method_type: Mapped[str] = mapped_column(String(20))
    last4: Mapped[str] = mapped_column(String(4))
    brand: Mapped[str | None] = mapped_column(String(50), nullable=True)
    exp_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exp_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_default: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default=PaymentMethodStatus.ACTIVE.value)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    charges: Mapped[list["Charge"]] = relationship(back_populates="payment_method")


class Charge(Base):
    """idempotency_key carries a real UNIQUE constraint — this is not
    just an application-level check. A retried request with the same key
    can race the first attempt at the database level (two workers, a
    client retry storm) and only the constraint, not a prior SELECT,
    guarantees exactly one row ever exists for it."""

    __tablename__ = "charges"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_charge_tenant_idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, index=True)
    payment_method_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("payment_methods.id"), index=True)

    idempotency_key: Mapped[str] = mapped_column(String(255), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="usd")
    status: Mapped[str] = mapped_column(String(30), default=ChargeStatus.PENDING.value, index=True)
    provider_mode: Mapped[str] = mapped_column(String(20))
    provider_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    refunded_amount_cents: Mapped[int] = mapped_column(Integer, default=0)

    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    payment_method: Mapped["PaymentMethod"] = relationship(back_populates="charges")
    refunds: Mapped[list["Refund"]] = relationship(back_populates="charge")


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    charge_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("charges.id"), index=True)

    amount_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=RefundStatus.SUCCEEDED.value)
    provider_mode: Mapped[str] = mapped_column(String(20))
    provider_refund_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    charge: Mapped["Charge"] = relationship(back_populates="refunds")


class WebhookEvent(Base):
    """provider_event_id carries a UNIQUE constraint — webhook delivery
    is at-least-once by design in every real processor (Stripe explicitly
    documents this), so processing must be idempotent at the storage
    layer, not just by convention in application code."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_webhook_provider_event"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    provider_event_id: Mapped[str] = mapped_column(String(255), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    payload_json: Mapped[str] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
