import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import GUID, Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LeadStatus(str, enum.Enum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    CONVERTED = "converted"


class DealStage(str, enum.Enum):
    PROSPECTING = "prospecting"
    QUALIFICATION = "qualification"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"


OPEN_STAGES = (DealStage.PROSPECTING.value, DealStage.QUALIFICATION.value, DealStage.PROPOSAL.value, DealStage.NEGOTIATION.value)
CLOSED_STAGES = (DealStage.CLOSED_WON.value, DealStage.CLOSED_LOST.value)


class ActivityType(str, enum.Enum):
    CALL = "call"
    EMAIL = "email"
    NOTE = "note"
    MEETING = "meeting"


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)

    first_name: Mapped[str] = mapped_column(String(150))
    last_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(100), default="unknown")

    status: Mapped[str] = mapped_column(String(20), default=LeadStatus.NEW.value, index=True)
    disqualify_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    deals: Mapped[list["Deal"]] = relationship(back_populates="lead")
    activities: Mapped[list["Activity"]] = relationship(back_populates="lead")


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("leads.id"), nullable=True, index=True)

    name: Mapped[str] = mapped_column(String(255))
    stage: Mapped[str] = mapped_column(String(20), default=DealStage.PROSPECTING.value, index=True)
    value_cents: Mapped[int] = mapped_column(Integer)
    mobility_class: Mapped[str | None] = mapped_column(String(30), nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(GUID, index=True)

    expected_close_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    lead: Mapped["Lead"] = relationship(back_populates="deals")
    activities: Mapped[list["Activity"]] = relationship(back_populates="deal")


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("deals.id"), nullable=True, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("leads.id"), nullable=True, index=True)

    activity_type: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(GUID)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    deal: Mapped["Deal"] = relationship(back_populates="activities")
    lead: Mapped["Lead"] = relationship(back_populates="activities")
