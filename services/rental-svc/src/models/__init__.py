"""
rental-svc data model — fleet, reservations, real-time utilization,
demand forecasting, dynamic pricing history, and predictive insights
for both the renter and the hub administrator.

Governing principle, same as everywhere else on this platform: full
automation by default. Vehicle allocation, dynamic pricing, demand
forecasting, and insight generation all execute autonomously with no
approval gate — none of it is an access-control decision. The one
structural rule enforced rather than left to convention:
modify_reservation refuses a rate change once a reservation is ACTIVE
or COMPLETED — a renter who has already taken the vehicle can never
have their price retroactively altered, the same way a subscription's
already-billed period is never silently revised elsewhere on this
platform.

The pricing model is deliberately two-factor, not one: SUPPLY
(utilization — how full a hub is right now) and DEMAND (booking
velocity and trend — how fast it's filling). A hub at 70% utilization
that has been flat for a week and one that hit 70% in the last six
hours are in genuinely different positions; utilization alone can't
tell them apart, which is why UtilizationSnapshot and BookingEvent are
separate tables rather than one.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, GUID


class RentalClass(str, enum.Enum):
    URBAN = "urban"
    FAMILY = "family"
    EXECUTIVE = "executive"
    ADVENTURE = "adventure"
    LUXURY = "luxury"
    PERFORMANCE = "performance"


# ══════════════════════════════════════════════════════════════════
# Fleet
# ══════════════════════════════════════════════════════════════════

class VehicleStatus(str, enum.Enum):
    AVAILABLE = "available"
    RENTED = "rented"
    MAINTENANCE = "maintenance"


class RentalVehicle(Base):
    __tablename__ = "rental_vehicles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rental_class: Mapped[RentalClass] = mapped_column(Enum(RentalClass), nullable=False)
    vin: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    base_daily_rate_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[VehicleStatus] = mapped_column(Enum(VehicleStatus), nullable=False, default=VehicleStatus.AVAILABLE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Reservations
# ══════════════════════════════════════════════════════════════════

class ReservationStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Reservation(Base):
    """vehicle_id is null until autonomous allocation assigns one at
    confirmation — see reservation_engine.confirm_reservation. Once
    status reaches ACTIVE or COMPLETED, daily_rate_usd is structurally
    frozen: modify_reservation raises rather than silently refusing,
    so a caller can't mistake a no-op for a successful change."""

    __tablename__ = "reservations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rental_class: Mapped[RentalClass] = mapped_column(Enum(RentalClass), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("rental_vehicles.id"), nullable=True)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    daily_rate_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(Enum(ReservationStatus), nullable=False, default=ReservationStatus.PENDING)
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Demand signal — the raw time series booking_velocity is computed
# from. Distinct from Reservation itself so a cancelled booking still
# counts as a real demand signal that happened, even after the
# reservation record it produced is gone.
# ══════════════════════════════════════════════════════════════════

class BookingEventType(str, enum.Enum):
    BOOKED = "booked"
    CANCELLED = "cancelled"


class BookingEvent(Base):
    __tablename__ = "booking_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rental_class: Mapped[RentalClass] = mapped_column(Enum(RentalClass), nullable=False)
    event_type: Mapped[BookingEventType] = mapped_column(Enum(BookingEventType), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Utilization — the supply-side snapshot
# ══════════════════════════════════════════════════════════════════

class UtilizationSnapshot(Base):
    __tablename__ = "utilization_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rental_class: Mapped[RentalClass] = mapped_column(Enum(RentalClass), nullable=False)
    booked_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    utilization_pct: Mapped[float] = mapped_column(Float, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Demand forecast — predictive analytics, autonomously generated
# ══════════════════════════════════════════════════════════════════

class DemandTrend(str, enum.Enum):
    ACCELERATING = "accelerating"
    STEADY = "steady"
    DECELERATING = "decelerating"


class DemandForecast(Base):
    __tablename__ = "demand_forecasts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rental_class: Mapped[RentalClass] = mapped_column(Enum(RentalClass), nullable=False)
    booking_velocity_per_day: Mapped[float] = mapped_column(Float, nullable=False)
    trend: Mapped[DemandTrend] = mapped_column(Enum(DemandTrend), nullable=False)
    forecasted_utilization_pct: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Pricing adjustment history — every dynamic rate change, with the
# full supply+demand reasoning attached, not just the resulting number
# ══════════════════════════════════════════════════════════════════

class PricingAdjustmentLog(Base):
    __tablename__ = "pricing_adjustment_logs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reservation_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("reservations.id"), nullable=False)
    old_rate_usd: Mapped[float] = mapped_column(Float, nullable=False)
    new_rate_usd: Mapped[float] = mapped_column(Float, nullable=False)
    utilization_pct: Mapped[float] = mapped_column(Float, nullable=False)
    supply_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    demand_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    combined_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    adjusted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    adjusted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Predictive insights — instructive to the renter and the
# administrator, generated from the same demand+supply data
# ══════════════════════════════════════════════════════════════════

class ClientInsightType(str, enum.Enum):
    BOOK_NOW = "book_now"
    PRICE_RISING = "price_rising"
    PRICE_FALLING = "price_falling"
    LOW_AVAILABILITY = "low_availability"
    GOOD_VALUE = "good_value"


class ClientInsight(Base):
    """Instructive to the renter — a plain-language nudge derived
    from the same forecast data the pricing engine uses, not a
    separate guess."""

    __tablename__ = "client_insights"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rental_class: Mapped[RentalClass] = mapped_column(Enum(RentalClass), nullable=False)
    insight_type: Mapped[ClientInsightType] = mapped_column(Enum(ClientInsightType), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AdminInsightType(str, enum.Enum):
    CAPACITY_RISK = "capacity_risk"
    REALLOCATION_OPPORTUNITY = "reallocation_opportunity"
    DEMAND_SPIKE = "demand_spike"
    UNDERUTILIZED = "underutilized"


class AdminInsightSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AdminInsight(Base):
    """Instructive to the hub administrator — a predictive, actionable
    flag (capacity risk ahead, an underutilized hub that could donate
    vehicles) rather than a raw metric the admin has to interpret
    themselves."""

    __tablename__ = "admin_insights"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rental_class: Mapped[RentalClass] = mapped_column(Enum(RentalClass), nullable=False)
    insight_type: Mapped[AdminInsightType] = mapped_column(Enum(AdminInsightType), nullable=False)
    severity: Mapped[AdminInsightSeverity] = mapped_column(Enum(AdminInsightSeverity), nullable=False, default=AdminInsightSeverity.INFO)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
