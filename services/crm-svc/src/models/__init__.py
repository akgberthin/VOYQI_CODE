import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
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

    # CRM-002 — written by the Revenue Agent, never by the capture itself.
    external_ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    routed_to_team: Mapped[str | None] = mapped_column(String(64), nullable=True)

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


# ---------------------------------------------------------------------------
# Enterprise CRM intelligence — the records ai-orchestration-svc's Revenue
# Agent (CRM-001 identity resolution, CRM-007 profile dedup) and Concierge
# Agent (CRM-003 inbox) read and write. The agents only ever *propose*
# (match candidates, duplicate candidates, draft replies); merging
# identities and sending a reply are human actions.
# ---------------------------------------------------------------------------


class CandidateStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ConversationStatus(str, enum.Enum):
    OPEN = "open"
    ESCALATED = "escalated"
    CLOSED = "closed"


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class DraftReplyStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    DISCARDED = "discarded"


class PersonProfile(Base):
    """One resolved real-world person across every intake channel."""

    __tablename__ = "person_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SourceRecord(Base):
    """A raw identity record from one channel (web form, rental booking,
    dealership walk-in...) awaiting resolution to a PersonProfile."""

    __tablename__ = "source_records"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    source_system: Mapped[str] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_profile_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("person_profiles.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class MatchCandidate(Base):
    __tablename__ = "match_candidates"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    source_record_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("source_records.id"), index=True)
    # None = the agent found no good existing match; confirming then
    # creates a new profile for the record.
    candidate_person_profile_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("person_profiles.id"), nullable=True)
    match_score: Mapped[float] = mapped_column(Float)
    match_reasoning: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=CandidateStatus.PENDING.value, index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProfileDuplicateCandidate(Base):
    __tablename__ = "profile_duplicate_candidates"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    profile_a_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("person_profiles.id"))
    profile_b_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("person_profiles.id"))
    match_score: Mapped[float] = mapped_column(Float)
    match_reasoning: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=CandidateStatus.PENDING.value, index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    person_profile_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("person_profiles.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(32), default="email")
    subject: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default=ConversationStatus.OPEN.value, index=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    routed_to_team: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("conversations.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10))
    body: Mapped[str] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class DraftReply(Base):
    __tablename__ = "draft_replies"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("conversations.id"), index=True)
    in_reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("messages.id"), nullable=True)
    draft_body: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=DraftReplyStatus.PENDING.value, index=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_message_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
