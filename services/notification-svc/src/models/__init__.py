import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Channel(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED_OPTED_OUT = "skipped_opted_out"


class DeliveryMode(str, enum.Enum):
    """Honest accounting of HOW a notification was actually delivered.
    CONSOLE means no real provider (SMTP host, SMS API key) is configured
    for this environment — the notification is rendered and logged, not
    actually delivered, and callers can see that distinction rather than
    being told something was "sent" when nothing left the building."""

    REAL_PROVIDER = "real_provider"
    CONSOLE = "console"


class NotificationCategory(str, enum.Enum):
    TRANSACTIONAL = "transactional"
    MARKETING = "marketing"
    SECURITY = "security"  # never opt-out-able — e.g. "your password was changed"


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "key", "channel", name="uq_template_tenant_key_channel"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    key: Mapped[str] = mapped_column(String(100), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(20), default=NotificationCategory.TRANSACTIONAL.value)
    subject_template: Mapped[str | None] = mapped_column(String(500), nullable=True)  # email only
    body_template: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    template_key: Mapped[str] = mapped_column(String(100), index=True)
    recipient: Mapped[str] = mapped_column(String(320), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)

    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(30), default=NotificationStatus.PENDING.value, index=True)
    delivery_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationPreference(Base):
    """Per-user, per-category, per-channel opt-in state. SECURITY category
    notifications bypass this entirely (enforced in the engine) — there is
    no opting out of being told your password changed or a suspicious
    login occurred."""

    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "category", "channel", name="uq_pref_user_category_channel"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, index=True)
    category: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(20))
    opted_in: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
