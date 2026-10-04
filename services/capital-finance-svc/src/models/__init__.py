import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class FundingSourceType(str, enum.Enum):
    DEBT = "debt"
    EQUITY = "equity"
    CREDIT_LINE = "credit_line"
    OTHER = "other"


class FundingSourceStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class CapitalMetricDomain(str, enum.Enum):
    BALANCE = "balance"
    UTILIZATION = "utilization"
    COST_OF_CAPITAL = "cost_of_capital"
    COVENANT_HEADROOM = "covenant_headroom"


class ThresholdComparison(str, enum.Enum):
    GTE = "gte"
    LTE = "lte"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class FundingSource(Base):
    """A registered capital source — a debt facility, equity injection,
    or credit line. The registry capital-finance-svc rolls balance,
    utilization, cost-of-capital, and covenant-headroom metrics up
    against."""

    __tablename__ = "funding_sources"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[FundingSourceType] = mapped_column(Enum(FundingSourceType), nullable=False)
    status: Mapped[FundingSourceStatus] = mapped_column(
        Enum(FundingSourceStatus), nullable=False, default=FundingSourceStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapitalRollupSnapshot(Base):
    """A periodic report for one funding source. capital-finance-svc
    never reads another service's database directly (database-per-
    service); each snapshot is something a source service or treasury
    admin tool explicitly reported in."""

    __tablename__ = "capital_rollup_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    funding_source_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_domain: Mapped[CapitalMetricDomain] = mapped_column(
        Enum(CapitalMetricDomain), nullable=False, index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_service: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapitalKPIThreshold(Base):
    """Purely reporting config — tier 1, no approval gate (Portfolio
    Intelligence group). Note: this is distinct from an actual capital
    allocation or financing DECISION, which is not in this service's
    scope — see docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §4."""

    __tablename__ = "capital_kpi_thresholds"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metric_domain: Mapped[CapitalMetricDomain] = mapped_column(Enum(CapitalMetricDomain), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    comparison: Mapped[ThresholdComparison] = mapped_column(Enum(ThresholdComparison), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapitalKPIAlert(Base):
    """Evaluated automatically the instant a matching snapshot lands.
    Purely informational — tier 1. A covenant-headroom breach, for
    example, is flagged here for a human to review — it is not itself
    a financing decision."""

    __tablename__ = "capital_kpi_alerts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    funding_source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_domain: Mapped[CapitalMetricDomain] = mapped_column(Enum(CapitalMetricDomain), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    comparison: Mapped[ThresholdComparison] = mapped_column(Enum(ThresholdComparison), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), nullable=False, default=AlertStatus.OPEN)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
