import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class PolicyStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class SubjectType(str, enum.Enum):
    USER = "user"
    ROLE = "role"


class GrantRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CompliancePolicy(Base):
    """A registered ABAC-style access policy. Defining or updating a
    policy's description is not itself a grant of access, so it
    carries no approval gate — only *applying* a policy grant does."""

    __tablename__ = "compliance_policies"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[PolicyStatus] = mapped_column(Enum(PolicyStatus), nullable=False, default=PolicyStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyGrantRequest(Base):
    """Applying an ABAC policy grant is the platform's one hard,
    unconditional gate — tier 3, no threshold, no AI code path reaches
    it (docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §4:
    "abac.policy_grant_apply: tier 3, never auto-applies an access-
    policy change"). Two things are structurally required before
    `status` can become APPROVED, both enforced in grant_engine.py,
    not just documented:

    1. `simulated` must be True — a dry run must have been recorded
       first (`simulation_result` populated), so an admin approves a
       known effect, not a guess.
    2. `accountable_role` must be set — a named accountable human role,
       not just "someone clicked approve".

    There is no risk score and no configurable threshold here at all —
    unlike a tier-2 gate, nothing about this can be tuned into
    auto-approval."""

    __tablename__ = "policy_grant_requests"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("compliance_policies.id"), nullable=False, index=True)
    subject_type: Mapped[SubjectType] = mapped_column(Enum(SubjectType), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)

    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    simulation_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[GrantRequestStatus] = mapped_column(
        Enum(GrantRequestStatus), nullable=False, default=GrantRequestStatus.PENDING
    )
    accountable_role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccessSessionStatus(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"


class BreakGlassSession(Base):
    """Emergency elevated access. Activating one is never blocked — it
    exists for emergencies — but every activation is published as
    access.break_glass_activated, and the Compliance/Audit Agent can
    recommend ending it early; a human approving that recommendation
    is what ends it."""

    __tablename__ = "break_glass_sessions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    activated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    off_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[AccessSessionStatus] = mapped_column(
        Enum(AccessSessionStatus), nullable=False, default=AccessSessionStatus.ACTIVE
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class ImpersonationSession(Base):
    """An admin acting as another user (support/debugging). Same
    lifecycle as break-glass; risk rises when there's no session
    recording reference attached."""

    __tablename__ = "impersonation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    admin_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    session_recording_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    off_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[AccessSessionStatus] = mapped_column(
        Enum(AccessSessionStatus), nullable=False, default=AccessSessionStatus.ACTIVE
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
