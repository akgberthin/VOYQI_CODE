"""
Hub Network Administration — HUB-001 through HUB-003.

Deliberate architectural seam: fleet-svc already has its own thin Hub
record (FLT-003, just id/name/region) that vehicles' current_hub_id
points at for staging purposes — that stays untouched to avoid
disturbing an already-tested, working call chain (fleet_tools,
transfer orders). This service's Hub is the richer HUB-001
administrative profile (type, geo, hours, amenities, capacity) that the
enterprise spec actually calls for. In a real deployment the two would
share the same id space (hub-svc becomes the record of truth and
fleet-svc's thin table becomes a cached FK target) — that reconciliation
is a real, explicit follow-up, not silently glossed over.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class HubType(str, enum.Enum):
    AIRPORT = "airport"
    CITY_BUSINESS = "city_business"
    RESIDENTIAL = "residential"
    UNIVERSITY_CAMPUS = "university_campus"
    CORPORATE_CAMPUS = "corporate_campus"
    PARTNER_WHITE_LABEL = "partner_white_label"


class Hub(Base):
    """HUB-001 — the administrative record of truth for a physical hub:
    profile, capacity, hours, amenities, and the geo coordinates the
    member-app and operator-portal maps both render directly against."""

    __tablename__ = "hubs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    hub_type: Mapped[HubType] = mapped_column(Enum(HubType), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    operating_hours_open: Mapped[str] = mapped_column(String(8), nullable=False, default="06:00")
    operating_hours_close: Mapped[str] = mapped_column(String(8), nullable=False, default="22:00")
    has_lounge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    amenities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tenant_entity_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class TouchpointEventType(str, enum.Enum):
    PICKUP = "pickup"
    RETURN = "return"
    SWAP = "swap"


class TouchpointSource(str, enum.Enum):
    SUBSCRIPTION = "subscription"
    RENTAL = "rental"


class TouchpointStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    EXCEPTION = "exception"


class TouchpointEvent(Base):
    """HUB-002 — one line on the daily hub operations board: a
    subscription or rental pickup/return/swap. exception_reason is set
    when staff flag something wrong (no vehicle staged, member no-show,
    damage found) — the operational counterpart to a compliance
    exception, reviewed by hub staff rather than an agent."""

    __tablename__ = "touchpoint_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    event_type: Mapped[TouchpointEventType] = mapped_column(Enum(TouchpointEventType), nullable=False)
    source: Mapped[TouchpointSource] = mapped_column(Enum(TouchpointSource), nullable=False)
    member_or_renter_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[TouchpointStatus] = mapped_column(Enum(TouchpointStatus), nullable=False, default=TouchpointStatus.SCHEDULED)
    exception_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ChecklistStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"


class VehiclePrepChecklist(Base):
    """A prep checklist tied to one touchpoint event. Mirrors dms-svc's
    ReconOrder/ReconWorkItem pattern deliberately: mark_ready is a hard
    gate that refuses while any PrepChecklistItem is still open — the
    same "can't skip the checklist" discipline used for recon and
    rental checkout elsewhere on this platform."""

    __tablename__ = "vehicle_prep_checklists"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    touchpoint_event_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    status: Mapped[ChecklistStatus] = mapped_column(Enum(ChecklistStatus), nullable=False, default=ChecklistStatus.PENDING)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PrepChecklistItem(Base):
    __tablename__ = "prep_checklist_items"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    checklist_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("vehicle_prep_checklists.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CardStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class VoyqiCard(Base):
    """The physical/digital credential (NXP DESFire EV3 per platform
    spec). status flips to SUSPENDED automatically — never manually —
    the moment a geographic anomaly is detected on a tap; reactivation
    is a distinct human action (verify the member out of band), not an
    override of the finding."""

    __tablename__ = "voyqi_cards"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    card_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    status: Mapped[CardStatus] = mapped_column(Enum(CardStatus), nullable=False, default=CardStatus.ACTIVE)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CardTapEvent(Base):
    """Every tap is logged, per platform spec ('AI logs every tap with
    timestamp'). anomaly_flagged/anomaly_reason record the outcome of
    the geographic-plausibility check run against the member's
    immediately preceding tap."""

    __tablename__ = "card_tap_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    card_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    tapped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    anomaly_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    anomaly_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
