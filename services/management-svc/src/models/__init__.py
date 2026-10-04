import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class CostCenterCategory(str, enum.Enum):
    IT = "it"
    FACILITIES = "facilities"
    PROCUREMENT = "procurement"
    SHARED_STAFF = "shared_staff"
    OTHER = "other"


class CostCenterStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class OpsMetricDomain(str, enum.Enum):
    SPEND = "spend"
    HEADCOUNT = "headcount"
    UTILIZATION = "utilization"
    SLA = "sla"


class ThresholdComparison(str, enum.Enum):
    GTE = "gte"
    LTE = "lte"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class CostCenter(Base):
    """A shared-services cost center — IT, facilities, procurement, or a
    shared-staff pool that serves more than one subsidiary. This is the
    registry management-svc rolls operational metrics up against;
    without it, a subsidiary_key on a snapshot would be meaningless."""

    __tablename__ = "cost_centers"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[CostCenterCategory] = mapped_column(Enum(CostCenterCategory), nullable=False)
    status: Mapped[CostCenterStatus] = mapped_column(
        Enum(CostCenterStatus), nullable=False, default=CostCenterStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpsRollupSnapshot(Base):
    """A periodic operational metrics report for one cost center — spend,
    headcount, utilization, or SLA performance. management-svc never
    reads another service's database directly (database-per-service);
    each snapshot is something a source service or admin tool explicitly
    reported in."""

    __tablename__ = "ops_rollup_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cost_center_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_domain: Mapped[OpsMetricDomain] = mapped_column(Enum(OpsMetricDomain), nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_service: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpsKPIThreshold(Base):
    """Defines what counts as a breach for one metric key within a
    domain, scoped to shared-services operations. Purely reporting
    config — tier 1, no approval gate (Portfolio Intelligence group,
    same as executive-svc)."""

    __tablename__ = "ops_kpi_thresholds"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_domain: Mapped[OpsMetricDomain] = mapped_column(Enum(OpsMetricDomain), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    comparison: Mapped[ThresholdComparison] = mapped_column(Enum(ThresholdComparison), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpsKPIAlert(Base):
    """Evaluated automatically the instant a matching snapshot lands.
    Purely informational — tier 1 — nothing downstream requires
    approval to raise or clear one."""

    __tablename__ = "ops_kpi_alerts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cost_center_key: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_domain: Mapped[OpsMetricDomain] = mapped_column(Enum(OpsMetricDomain), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    comparison: Mapped[ThresholdComparison] = mapped_column(Enum(ThresholdComparison), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), nullable=False, default=AlertStatus.OPEN)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
