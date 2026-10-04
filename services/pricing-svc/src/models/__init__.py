import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import GUID, Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MobilityClass(str, enum.Enum):
    URBAN = "urban"
    FAMILY = "family"
    EXECUTIVE = "executive"
    ADVENTURE = "adventure"
    LUXURY = "luxury"
    PERFORMANCE = "performance"
    LUXURY_PLUS = "luxury_plus"
    PERFORMANCE_PLUS = "performance_plus"


class DiscountType(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class RateCard(Base):
    """A rate card is never mutated once created — "changing a price"
    means closing out the current row's effective_to and inserting a
    new one. This is a real Type-2 slowly-changing-dimension pattern:
    price history is inherent to the schema itself, not reconstructed
    from a bolted-on audit log that could drift out of sync with what
    the price actually was at any given moment."""

    __tablename__ = "rate_cards"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    mobility_class: Mapped[str] = mapped_column(String(30), index=True)
    market: Mapped[str] = mapped_column(String(100), default="national", index=True)

    price_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="usd")

    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    changed_by: Mapped[str] = mapped_column(String(255))  # a user id, or "ai_orchestration" / "system"
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PromoCode(Base):
    __tablename__ = "promo_codes"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_promo_tenant_code"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    code: Mapped[str] = mapped_column(String(50), index=True)

    discount_type: Mapped[str] = mapped_column(String(20))
    discount_value: Mapped[int] = mapped_column(Integer)  # percentage as whole number (10 = 10%), or cents for fixed_amount
    applicable_classes: Mapped[str] = mapped_column(String(500))  # comma-separated MobilityClass values, or "all"

    max_redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redemption_count: Mapped[int] = mapped_column(Integer, default=0)
    max_redemptions_per_user: Mapped[int] = mapped_column(Integer, default=1)

    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    redemptions: Mapped[list["PromoRedemption"]] = relationship(back_populates="promo_code")


class PromoRedemption(Base):
    __tablename__ = "promo_redemptions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    promo_code_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("promo_codes.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, index=True)
    order_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discount_applied_cents: Mapped[int] = mapped_column(Integer)
    redeemed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    promo_code: Mapped["PromoCode"] = relationship(back_populates="redemptions")
