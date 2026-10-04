import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import GUID, Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssetType(str, enum.Enum):
    PATENT = "patent"
    TRADEMARK = "trademark"
    COPYRIGHT = "copyright"
    TRADE_SECRET = "trade_secret"
    PROPRIETARY_SOFTWARE = "proprietary_software"


# Registered IP (patent/trademark/copyright) goes through a filing
# lifecycle. Trade secrets and unregistered proprietary software are
# protected by confidentiality from the moment they're recorded — there
# is no "filing" or "grant" step, and the only way they stop being
# protected is if the secret is actually compromised (disclosed).
REGISTERABLE_TYPES = (AssetType.PATENT.value, AssetType.TRADEMARK.value, AssetType.COPYRIGHT.value)
CONFIDENTIALITY_BASED_TYPES = (AssetType.TRADE_SECRET.value, AssetType.PROPRIETARY_SOFTWARE.value)


class AssetStatus(str, enum.Enum):
    PENDING = "pending"  # registered types only: filed, awaiting grant
    GRANTED = "granted"  # registered types only
    ABANDONED = "abandoned"  # registered types only: withdrawn before grant
    EXPIRED = "expired"  # registered types only: term ended
    PROTECTED = "protected"  # confidentiality-based types only
    COMPROMISED = "compromised"  # confidentiality-based types only: the secret leaked — terminal, one-way


LICENSABLE_STATUSES = (AssetStatus.GRANTED.value, AssetStatus.PROTECTED.value)
TERMINAL_STATUSES = (AssetStatus.ABANDONED.value, AssetStatus.EXPIRED.value, AssetStatus.COMPROMISED.value)


class LicenseType(str, enum.Enum):
    EXCLUSIVE = "exclusive"
    NON_EXCLUSIVE = "non_exclusive"


class RoyaltyType(str, enum.Enum):
    PERCENTAGE_OF_REVENUE = "percentage_of_revenue"
    FIXED_FEE = "fixed_fee"
    NONE = "none"


class LicenseStatus(str, enum.Enum):
    ACTIVE = "active"
    TERMINATED = "terminated"
    EXPIRED = "expired"


class RoyaltyStatementStatus(str, enum.Enum):
    DRAFT = "draft"
    FINALIZED = "finalized"
    PAID = "paid"


class IPAsset(Base):
    """owning_entity is always the IP-holding subsidiary (e.g. "VOYQI
    Technology LLC") — the whole point of this table is to record what
    that entity owns so it can license it to the operating subsidiaries,
    which is exactly what LicenseAgreement rows represent."""

    __tablename__ = "ip_assets"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)

    asset_type: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owning_entity: Mapped[str] = mapped_column(String(255))

    registration_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(100), nullable=True)
    filing_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grant_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expiration_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(30), index=True)
    status_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    licenses: Mapped[list["LicenseAgreement"]] = relationship(back_populates="ip_asset")


class LicenseAgreement(Base):
    """An intercompany license — the IP-holding entity (via the asset's
    owning_entity) licensing an asset to one of the operating
    subsidiaries. An active exclusive license blocks any other active
    license (exclusive or non-exclusive) on the same asset; multiple
    non-exclusive licenses can coexist."""

    __tablename__ = "license_agreements"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    ip_asset_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("ip_assets.id"), index=True)

    licensee_entity: Mapped[str] = mapped_column(String(255), index=True)
    license_type: Mapped[str] = mapped_column(String(20))
    royalty_type: Mapped[str] = mapped_column(String(30))
    royalty_rate: Mapped[int] = mapped_column(Integer, default=0)  # basis points (500 = 5.00%) for percentage; cents for fixed_fee

    effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expiration_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default=LicenseStatus.ACTIVE.value, index=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    termination_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ip_asset: Mapped["IPAsset"] = relationship(back_populates="licenses")
    royalty_statements: Mapped[list["RoyaltyStatement"]] = relationship(back_populates="license_agreement")


class RoyaltyStatement(Base):
    __tablename__ = "royalty_statements"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    license_agreement_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("license_agreements.id"), index=True)

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    licensee_reported_revenue_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    royalty_owed_cents: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[str] = mapped_column(String(20), default=RoyaltyStatementStatus.DRAFT.value, index=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    license_agreement: Mapped["LicenseAgreement"] = relationship(back_populates="royalty_statements")
