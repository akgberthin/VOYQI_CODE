import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class PolicyStatus(str, enum.Enum):
    ACTIVE = "active"
    LAPSED = "lapsed"
    CANCELLED = "cancelled"


class RiskBand(str, enum.Enum):
    LOW = "low"
    ELEVATED = "elevated"
    HIGH = "high"


class RiskPolicy(Base):
    """An insured policy under risk oversight — the registry
    insurance-risk-svc scores and flags against. Registering or
    updating a policy's status is not itself a risk judgment, so it
    carries no approval gate."""

    __tablename__ = "risk_policies"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_type: Mapped[str] = mapped_column(String(64), nullable=False)
    insured_name: Mapped[str] = mapped_column(String(128), nullable=False)
    coverage_amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[PolicyStatus] = mapped_column(Enum(PolicyStatus), nullable=False, default=PolicyStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RiskAssessment(Base):
    """A deterministic risk score run against one policy — tier 1,
    per docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §4: "anomaly
    flagging and policy simulation are tier 1" for the Compliance &
    Risk Intelligence group. This is flagging/scoring only — it never
    itself changes a policy's coverage, premium, or status; that
    remains a human underwriting decision outside this service's
    scope."""

    __tablename__ = "risk_assessments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("risk_policies.id"), nullable=False, index=True)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[RiskBand] = mapped_column(Enum(RiskBand), nullable=False)
    drivers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
