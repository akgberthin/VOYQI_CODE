import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class MetricDomain(str, enum.Enum):
    REVENUE = "revenue"
    FLEET = "fleet"
    WORKFORCE = "workforce"
    MARKETING = "marketing"


class ThresholdComparison(str, enum.Enum):
    GTE = "gte"
    LTE = "lte"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class RollupSnapshot(Base):
    """A periodic rollup reported in by another service for one
    subsidiary and one metric domain. executive-svc never reads another
    service's database directly — database-per-service means every
    cross-subsidiary number here is something a source service
    explicitly pushed, not something joined live across tenants."""

    __tablename__ = "rollup_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subsidiary_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_domain: Mapped[MetricDomain] = mapped_column(Enum(MetricDomain), nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_service: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KPIThreshold(Base):
    """Defines what counts as a breach for one metric key within a
    domain. Purely a reporting config — applying or editing a threshold
    is not an access-control or financial-commitment decision, so it
    carries no approval gate (Portfolio Intelligence is tier 1 across
    the board per the platform's autonomy tiering spec)."""

    __tablename__ = "kpi_thresholds"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_domain: Mapped[MetricDomain] = mapped_column(Enum(MetricDomain), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    comparison: Mapped[ThresholdComparison] = mapped_column(Enum(ThresholdComparison), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KPIAlert(Base):
    """Evaluated automatically the instant a matching snapshot lands.
    Purely informational — tier 1 — because nothing downstream requires
    approval to raise or clear one; there is no approval-gated action in
    Portfolio Intelligence's current scope."""

    __tablename__ = "kpi_alerts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subsidiary_key: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_domain: Mapped[MetricDomain] = mapped_column(Enum(MetricDomain), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    comparison: Mapped[ThresholdComparison] = mapped_column(Enum(ThresholdComparison), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), nullable=False, default=AlertStatus.OPEN)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
