import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class ApplicationStatus(str, enum.Enum):
    DRAFT = "draft"
    SCORED = "scored"
    PENDING_DECISION = "pending_decision"
    APPROVED = "approved"
    DECLINED = "declined"


class LeadBand(str, enum.Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class CreditDecisionStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"


class LoanApplication(Base):
    """A loan/financing application moving through Sales &
    Originations Intelligence. Registering an application is not a
    decision — lead.score_and_route and lender.match_recommend (tier
    1, deterministic) run against it freely; credit.decision (tier 3)
    is the one step that structurally cannot auto-execute."""

    __tablename__ = "loan_applications"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    applicant_name: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    annual_income_usd: Mapped[float] = mapped_column(Float, nullable=False)
    fico_score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus), nullable=False, default=ApplicationStatus.DRAFT
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApplicationScore(Base):
    """Lead scoring — tier 1 per docs/AUTONOMY_TIERING_ENGINEER_
    INSTRUCTIONS.md §4 ("lead.score_and_route: tier 1"). Deterministic,
    auto-executes, never itself approves or declines financing."""

    __tablename__ = "application_scores"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("loan_applications.id"), nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[LeadBand] = mapped_column(Enum(LeadBand), nullable=False)
    drivers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CreditDecision(Base):
    """The platform's other hard, unconditional tier-3 gate —
    docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §4: "credit decisions
    are tier 3, hard-coded, no exceptions — this is a fair-lending
    requirement." No risk score and no threshold apply here at all.
    `credit_decision_engine.decide` is the only place `status` can
    leave PENDING, and it refuses to do so without a named
    `accountable_role` — for a decline as much as an approval, since
    an adverse credit action needs accountable documentation under
    fair-lending law just as much as an approval does."""

    __tablename__ = "credit_decisions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    application_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("loan_applications.id"), nullable=False, index=True)
    status: Mapped[CreditDecisionStatus] = mapped_column(
        Enum(CreditDecisionStatus), nullable=False, default=CreditDecisionStatus.PENDING
    )
    accountable_role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# ORG-001..003 — the unified origination pipeline for subscription
# enrollments and rental-to-subscription conversions (retail finance has
# its own credit decisioning in dms-svc). Served under /api/v1/org, the
# paths ai-orchestration-svc's org_tools calls.
# ---------------------------------------------------------------------------


class OriginationType(str, enum.Enum):
    SUBSCRIPTION_ENROLLMENT = "subscription_enrollment"
    RENTAL_CONVERSION = "rental_conversion"


class OriginationCaseStatus(str, enum.Enum):
    OPEN = "open"
    PREQUALIFIED = "prequalified"
    NEEDS_REVIEW = "needs_review"
    INCOMPLETE = "incomplete"
    IN_DECISIONING = "in_decisioning"
    APPROVED = "approved"
    DECLINED = "declined"


class OrgDecisionStatus(str, enum.Enum):
    AWAITING_SCORE = "awaiting_score"
    SCORED = "scored"
    APPROVED = "approved"
    DECLINED = "declined"


class RecommendedOutcome(str, enum.Enum):
    APPROVE = "approve"
    DECLINE = "decline"
    MANUAL_REVIEW = "manual_review"


class OriginationCase(Base):
    __tablename__ = "origination_cases"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    origination_type: Mapped[OriginationType] = mapped_column(Enum(OriginationType), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Linked CRM lead / subscription / reservation reference — the
    # Revenue Agent looks up the lead score through it.
    external_ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[OriginationCaseStatus] = mapped_column(
        Enum(OriginationCaseStatus), nullable=False, default=OriginationCaseStatus.OPEN
    )
    prequal_verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prequal_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    prequal_next_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OriginationDecision(Base):
    """The agent applies a risk score (tier 1); the fixed thresholds in
    config turn it into a recommended outcome; only a human with a named
    accountable_role finalizes approve/decline (tier 3, credit.decision)."""

    __tablename__ = "origination_decisions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("origination_cases.id"), nullable=False, index=True)
    status: Mapped[OrgDecisionStatus] = mapped_column(
        Enum(OrgDecisionStatus), nullable=False, default=OrgDecisionStatus.AWAITING_SCORE
    )
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_outcome: Mapped[RecommendedOutcome | None] = mapped_column(Enum(RecommendedOutcome), nullable=True)
    accountable_role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrgFundingSource(Base):
    """A lender/program in the funding panel the Revenue Agent matches
    approved cases against (ORG-003). Distinct from capital-finance-svc's
    FundingSource, which is the holding company's own debt/equity."""

    __tablename__ = "org_funding_sources"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    applicable_origination_type: Mapped[OriginationType] = mapped_column(Enum(OriginationType), nullable=False, index=True)
    min_risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    max_amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    reserve_participation_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
