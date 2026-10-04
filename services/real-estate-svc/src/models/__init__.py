import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class PropertyType(str, enum.Enum):
    HUB = "hub"
    OFFICE = "office"
    WAREHOUSE = "warehouse"
    OTHER = "other"


class PropertyStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class RealEstateMetricDomain(str, enum.Enum):
    OCCUPANCY = "occupancy"
    LEASE_COST = "lease_cost"
    MAINTENANCE = "maintenance"
    UTILIZATION = "utilization"


class ThresholdComparison(str, enum.Enum):
    GTE = "gte"
    LTE = "lte"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class Property(Base):
    """A registered property in the hub/office/warehouse portfolio —
    the registry real-estate-svc rolls occupancy, lease cost,
    maintenance, and utilization metrics up against."""

    __tablename__ = "properties"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    property_type: Mapped[PropertyType] = mapped_column(Enum(PropertyType), nullable=False)
    status: Mapped[PropertyStatus] = mapped_column(
        Enum(PropertyStatus), nullable=False, default=PropertyStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PropertyRollupSnapshot(Base):
    """A periodic report for one property — real-estate-svc never reads
    another service's database directly (database-per-service); each
    snapshot is something a source service or admin tool explicitly
    reported in."""

    __tablename__ = "property_rollup_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    property_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_domain: Mapped[RealEstateMetricDomain] = mapped_column(
        Enum(RealEstateMetricDomain), nullable=False, index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_service: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PropertyKPIThreshold(Base):
    """Purely reporting config — tier 1, no approval gate (Portfolio
    Intelligence group, same as executive-svc / management-svc)."""

    __tablename__ = "property_kpi_thresholds"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_domain: Mapped[RealEstateMetricDomain] = mapped_column(Enum(RealEstateMetricDomain), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    comparison: Mapped[ThresholdComparison] = mapped_column(Enum(ThresholdComparison), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PropertyKPIAlert(Base):
    """Evaluated automatically the instant a matching snapshot lands.
    Purely informational — tier 1."""

    __tablename__ = "property_kpi_alerts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    property_key: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_domain: Mapped[RealEstateMetricDomain] = mapped_column(Enum(RealEstateMetricDomain), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    comparison: Mapped[ThresholdComparison] = mapped_column(Enum(ThresholdComparison), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), nullable=False, default=AlertStatus.OPEN)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
