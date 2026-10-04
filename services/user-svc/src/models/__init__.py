import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import GUID, Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserType(str, enum.Enum):
    MEMBER = "member"
    EMPLOYEE = "employee"
    ADMIN = "admin"
    DEALER = "dealer"
    PARTNER = "partner"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class AddressType(str, enum.Enum):
    BILLING = "billing"
    SHIPPING = "shipping"
    HOME = "home"


class User(Base):
    """The one canonical identity record per person. Domain-specific data
    (subscription plan, employee title, dealer license) stays in its own
    service's database, per database-per-service — this table exists to
    be the single thing every other service's user_id field can point at,
    which is exactly what auth-svc's UserCredential.user_id already
    assumes exists."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_type: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default=UserStatus.ACTIVE.value, index=True)

    first_name: Mapped[str] = mapped_column(String(150))
    last_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(320), index=True)  # display copy; auth-svc is authoritative for login
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    status_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    profile_links: Mapped[list["UserProfileLink"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    addresses: Mapped[list["UserAddress"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    preferences: Mapped[list["UserPreference"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserProfileLink(Base):
    """Loose-coupling record: this User corresponds to entity
    `linked_entity_id` of type `linked_entity_type` in `linked_service`'s
    own database (e.g. subscription-svc's MemberProfile, hrm-svc's
    EmployeeProfile). Never a real foreign key across service
    boundaries — database-per-service means no service can enforce
    referential integrity into another service's tables, only convention."""

    __tablename__ = "user_profile_links"
    __table_args__ = (UniqueConstraint("user_id", "linked_service", "linked_entity_type", name="uq_user_link_service_type"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("users.id"), index=True)
    linked_service: Mapped[str] = mapped_column(String(100))
    linked_entity_type: Mapped[str] = mapped_column(String(100))
    linked_entity_id: Mapped[uuid.UUID] = mapped_column(GUID)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="profile_links")


class UserAddress(Base):
    __tablename__ = "user_addresses"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("users.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    address_type: Mapped[str] = mapped_column(String(20))
    line1: Mapped[str] = mapped_column(String(255))
    line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(150))
    state: Mapped[str] = mapped_column(String(100))
    postal_code: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(2), default="US")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="addresses")


class UserPreference(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_preference_key"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="preferences")
