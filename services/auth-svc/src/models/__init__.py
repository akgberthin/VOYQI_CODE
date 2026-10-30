import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import GUID, Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FailureReason(str, enum.Enum):
    UNKNOWN_EMAIL = "unknown_email"
    INVALID_PASSWORD = "invalid_password"
    INVALID_MFA = "invalid_mfa"
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_INACTIVE = "account_inactive"


class UserCredential(Base):
    """Login credentials for one user, one tenant. Loosely coupled to
    user-svc's User.id by convention (user_id) rather than a cross-service
    FK — database-per-service means these are two independent tables that
    agree on an id, not a joined relationship."""

    __tablename__ = "user_credentials"
    # Login looks a credential up by (tenant, email) — two rows for the
    # same pair would make that login fail for good.
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_credentials_tenant_email"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # A new secret waits here until the user proves their app produces codes
    # for it, so starting a re-enrolment never breaks the MFA already in use.
    mfa_pending_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)

    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user_credential")


class RefreshToken(Base):
    """Only the SHA-256 hash of the token is ever stored — the raw token
    exists only in the HttpOnly cookie on the client. Rotation chain
    (replaced_by_id) lets reuse-of-a-revoked-token be detected, which is
    the standard signal for refresh-token theft."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_credential_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("user_credentials.id"), index=True)

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user_credential: Mapped["UserCredential"] = relationship(back_populates="refresh_tokens")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_credential_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("user_credentials.id"), index=True)

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LoginAuditLog(Base):
    """Every login attempt, success or failure — immutable, append-only.
    email_attempted is retained even for unknown emails (useful for
    detecting enumeration/credential-stuffing attempts) but
    user_credential_id stays null in that case."""

    __tablename__ = "login_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_credential_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)
    email_attempted: Mapped[str] = mapped_column(String(320))
    success: Mapped[bool] = mapped_column(Boolean)
    failure_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
