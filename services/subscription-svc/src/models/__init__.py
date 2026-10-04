"""
subscription-svc domain models.

VOYQI Mobility owns the customer relationship for the Portable
Mobility(TM) subscription: enrollment, billing, GU economy, and
lifecycle. Per the spec, member records here are meant to be "a view
into the Enterprise CRM (Section 4), scoped to subscription-specific
workflows" — CRM isn't built yet, so MemberProfile is self-contained for
now, with the fields CRM-001 would eventually own kept clearly separate
from the subscription-specific fields this service actually owns.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class MobilityClass(str, enum.Enum):
    URBAN = "urban"
    FAMILY = "family"
    EXECUTIVE = "executive"
    ADVENTURE = "adventure"
    LUXURY = "luxury"
    PERFORMANCE = "performance"
    LUXURY_PLUS = "luxury_plus"  # invitation-only premium class
    PERFORMANCE_PLUS = "performance_plus"  # invitation-only premium class


class ServiceLevel(str, enum.Enum):
    GOLD_PLUS = "gold_plus"
    PLATINUM_PLUS = "platinum_plus"
    EXECUTIVE_PLUS = "executive_plus"


class KycStatus(str, enum.Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


class MemberType(str, enum.Enum):
    """A personal member has no organization_id and is billed and
    governed individually — every other model in this file (GU
    ledger, subscriptions, vehicle swaps) applies identically to both
    types, since the mobility experience itself doesn't change. What
    changes for a corporate member is billing aggregation and
    AI-driven seat utilization monitoring — see CorporateAccount."""
    PERSONAL = "personal"
    CORPORATE = "corporate"


class MemberProfile(Base):
    """MOB-001 — the subscription-scoped member record. churn_risk_score
    and upgrade_readiness_score are written by the Member Experience
    Agent, never computed inline in a request handler — see
    member_experience_engine.py."""

    __tablename__ = "member_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(256), nullable=False)

    member_type: Mapped[MemberType] = mapped_column(Enum(MemberType), nullable=False, default=MemberType.PERSONAL)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("corporate_accounts.id"), nullable=True)

    kyc_status: Mapped[KycStatus] = mapped_column(Enum(KycStatus), nullable=False, default=KycStatus.PENDING)
    mobility_class: Mapped[MobilityClass | None] = mapped_column(Enum(MobilityClass), nullable=True)
    service_level: Mapped[ServiceLevel | None] = mapped_column(Enum(ServiceLevel), nullable=True)
    active_vehicle_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)

    gu_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lifetime_value_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    churn_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    upgrade_readiness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_voyqi_black: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CorporateBillingType(str, enum.Enum):
    CENTRALIZED = "centralized"  # one invoice for the whole account
    PER_EMPLOYEE = "per_employee"  # each employee's usage billed to the company separately


class CorporateAccountStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class CorporateAccount(Base):
    """MOB — corporate/campus accounts. seat_limit is enforced in
    corporate_engine.add_employee, not just displayed — adding an
    employee beyond the limit is refused, not silently allowed and
    reconciled later. utilization is monitored autonomously per
    employee (see EmployeeUtilizationFlag) rather than left for a
    corporate admin to notice on their own."""

    __tablename__ = "corporate_accounts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(256), nullable=False)
    billing_contact_email: Mapped[str] = mapped_column(String(256), nullable=False)
    billing_type: Mapped[CorporateBillingType] = mapped_column(Enum(CorporateBillingType), nullable=False, default=CorporateBillingType.CENTRALIZED)
    seat_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    status: Mapped[CorporateAccountStatus] = mapped_column(Enum(CorporateAccountStatus), nullable=False, default=CorporateAccountStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UtilizationFlagType(str, enum.Enum):
    UNDERUTILIZING = "underutilizing"  # candidate for seat reallocation
    OVERUTILIZING = "overutilizing"  # candidate for a class upgrade


class EmployeeUtilizationFlag(Base):
    """Written exclusively by corporate_engine.evaluate_utilization —
    the AI-driven per-employee monitoring the corporate account spec
    calls for. A flag is a real, actionable state (surfaced to the
    corporate admin for a seat-reallocation or upgrade decision), not
    just an internal score — see resolved/resolved_by for the human
    action taken on it."""

    __tablename__ = "employee_utilization_flags"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("corporate_accounts.id"), nullable=False)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("member_profiles.id"), nullable=False)
    flag_type: Mapped[UtilizationFlagType] = mapped_column(Enum(UtilizationFlagType), nullable=False)
    trips_last_30d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Plan(Base):
    """MOB-002 — one mobility class priced for one market."""

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mobility_class: Mapped[MobilityClass] = mapped_column(Enum(MobilityClass), nullable=False)
    market: Mapped[str] = mapped_column(String(64), nullable=False)
    monthly_price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    entitlements: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_invitation_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ClassEquivalency(Base):
    """MOB-002 — cross-market class continuity: which class in
    destination_market is equivalent to source_class when a member
    travels (used by MOB-008 Global Mobility Continuity)."""

    __tablename__ = "class_equivalencies"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_class: Mapped[MobilityClass] = mapped_column(Enum(MobilityClass), nullable=False)
    destination_market: Mapped[str] = mapped_column(String(64), nullable=False)
    equivalent_class: Mapped[MobilityClass] = mapped_column(Enum(MobilityClass), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ApplicationStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ENROLLED = "enrolled"


class Application(Base):
    """MOB-003 — enrollment application. Approval hands off to
    Sales & Originations for eligibility/financing checks (not built
    yet) before a subscription can actually be created — enroll() below
    enforces that an application must be APPROVED first."""

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    applicant_name: Mapped[str] = mapped_column(String(128), nullable=False)
    applicant_email: Mapped[str] = mapped_column(String(256), nullable=False)
    requested_class: Mapped[MobilityClass] = mapped_column(Enum(MobilityClass), nullable=False)
    market: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(Enum(ApplicationStatus), nullable=False, default=ApplicationStatus.SUBMITTED)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejection_reason: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    member_profile_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class VoyqiCard(Base):
    """VOYQI Card (DESFire EV3) issuance and binding — the physical/
    digital credential that pairs to a member and, via fleet-svc's
    ProvisioningRecord, to their vehicle."""

    __tablename__ = "voyqi_cards"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    card_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_bound: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class Subscription(Base):
    """MOB-007 — the lifecycle record. Pausing holds the class with no
    charge (per the PRD's stated pause semantics); cancelling requires a
    reason and can carry an exit survey."""

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(Enum(SubscriptionStatus), nullable=False, default=SubscriptionStatus.ACTIVE)

    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    exit_survey: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    win_back_offer_extended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class InvoiceStatus(str, enum.Enum):
    OPEN = "open"
    PAID = "paid"
    PAST_DUE = "past_due"
    REFUNDED = "refunded"


class Invoice(Base):
    """MOB-004 — one billing-cycle invoice. Dunning is modeled as
    retry_count incrementing on each failed payment attempt; past a
    threshold (see billing_engine.MAX_DUNNING_RETRIES) the invoice is
    left PAST_DUE for a human collections workflow rather than retried
    indefinitely."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    proration_credit_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tax_jurisdiction: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[InvoiceStatus] = mapped_column(Enum(InvoiceStatus), nullable=False, default=InvoiceStatus.OPEN)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billing_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    billing_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefundStatus(str, enum.Enum):
    ISSUED = "issued"


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    issued_by: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[RefundStatus] = mapped_column(Enum(RefundStatus), nullable=False, default=RefundStatus.ISSUED)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class GUEarnTrigger(str, enum.Enum):
    LOW_MILEAGE = "low_mileage"
    PAUSE = "pause"
    REFERRAL = "referral"
    OFF_PEAK = "off_peak"
    PROFILE_COMPLETION = "profile_completion"
    RATING = "rating"
    ANNIVERSARY = "anniversary"
    # Awarded autonomously by the Member Experience Agent when a
    # member's churn-risk score crosses the proactive-intervention
    # threshold — a distinct trigger from the winback flow, which
    # only ever fires when the member has already initiated a
    # cancellation and is a member-facing offer, not an autonomous
    # internal action.
    RETENTION_SAVE = "retention_save"


class GULedgerEntryType(str, enum.Enum):
    EARN = "earn"
    REDEEM = "redeem"


class GULedgerEntry(Base):
    """MOB-005 — every Growth Unit earn/redeem event. The member's
    gu_balance on MemberProfile is a running total; this table is the
    append-only trail that total is derived from, so fraud review always
    has the full history to check, not just the current number."""

    __tablename__ = "gu_ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    entry_type: Mapped[GULedgerEntryType] = mapped_column(Enum(GULedgerEntryType), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    earn_trigger: Mapped[GUEarnTrigger | None] = mapped_column(Enum(GUEarnTrigger), nullable=True)
    redemption_item: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    flagged_for_fraud: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class RedemptionCatalogItem(Base):
    __tablename__ = "redemption_catalog_items"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    gu_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SwapRequestStatus(str, enum.Enum):
    REQUESTED = "requested"
    STAGED = "staged"
    FULFILLED = "fulfilled"
    SLA_BREACHED = "sla_breached"
    CANCELLED = "cancelled"


class SwapRequest(Base):
    """MOB-006 — a member's request to swap their active vehicle. Class-
    eligibility is checked before the request can be staged; staging
    itself is a fleet-svc TransferOrder proposed by the Fleet
    Intelligence Agent — this table is the member-facing side of that
    same physical move."""

    __tablename__ = "swap_requests"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    requested_class: Mapped[MobilityClass] = mapped_column(Enum(MobilityClass), nullable=False)
    status: Mapped[SwapRequestStatus] = mapped_column(Enum(SwapRequestStatus), nullable=False, default=SwapRequestStatus.REQUESTED)
    fleet_transfer_order_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TravelDeclaration(Base):
    """MOB-008 — a member's declared upcoming trip. Resolving the
    destination-market equivalent class and staging a vehicle there is
    exactly the travel.activated event fleet-svc's Fleet Intelligence
    Agent already reacts to — this table is where the declaration itself
    lives before that event fires."""

    __tablename__ = "travel_declarations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    destination_market: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_equivalent_class: Mapped[MobilityClass | None] = mapped_column(Enum(MobilityClass), nullable=True)
    staged_vehicle_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    staged_hub_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BlackInvitationStatus(str, enum.Enum):
    INVITED = "invited"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class BlackInvitation(Base):
    """MOB-009 — VOYQI Black(TM) is a separate brand, never a tier or
    class (a naming distinction the platform enforces structurally, not
    just in copy) — its entitlement catalog and invitation flow are
    deliberately kept in their own tables rather than reusing Plan."""

    __tablename__ = "black_invitations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    status: Mapped[BlackInvitationStatus] = mapped_column(Enum(BlackInvitationStatus), nullable=False, default=BlackInvitationStatus.INVITED)
    invited_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BlackEntitlement(Base):
    __tablename__ = "black_entitlements"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(256), nullable=False, default="")


class ConciergeMode(str, enum.Enum):
    PROACTIVE = "proactive"
    REACTIVE = "reactive"
    ASSISTED = "assisted"


class ConciergeJourneyStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ConciergeJourney(Base):
    """MOB-010 — a white-glove journey (airport pickup, chauffeur,
    multi-leg travel, hotel coordination, event transportation) for
    Luxury Plus, Performance Plus, or VOYQI Black members. mode tracks
    whether the AI initiated this proactively, the member requested it
    reactively, or a concierge is assisting with AI suggestions."""

    __tablename__ = "concierge_journeys"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    journey_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[ConciergeMode] = mapped_column(Enum(ConciergeMode), nullable=False)
    status: Mapped[ConciergeJourneyStatus] = mapped_column(Enum(ConciergeJourneyStatus), nullable=False, default=ConciergeJourneyStatus.ACTIVE)
    assigned_concierge: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    satisfaction_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Deposit holds (§16.6 payments spec — "NOT YET BUILT" until now) ──
# $5,000–$25,000 holds for Luxury Plus / Performance Plus, per class.
# Placement and full release are deterministic policy, not an AI
# judgment call, so they need no agent involvement — same principle
# as payroll math elsewhere on this platform: no AI touches binding
# financial calculations that are just a fixed table lookup. Capturing
# part or all of a deposit (damage, violation) is the one genuine
# financial-risk judgment call, and is the only step gated for human
# review.
class DepositHoldStatus(str, enum.Enum):
    PLACED = "placed"
    RELEASED = "released"
    CAPTURE_PENDING_REVIEW = "capture_pending_review"
    CAPTURED = "captured"


DEPOSIT_HOLD_AMOUNTS_USD: dict[str, float] = {
    MobilityClass.LUXURY_PLUS.value: 10000.0,
    MobilityClass.PERFORMANCE_PLUS.value: 15000.0,
}


class DepositHold(Base):
    __tablename__ = "deposit_holds"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    mobility_class: Mapped[MobilityClass] = mapped_column(Enum(MobilityClass), nullable=False)
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    captured_amount_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    capture_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[DepositHoldStatus] = mapped_column(Enum(DepositHoldStatus), nullable=False, default=DepositHoldStatus.PLACED)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Upgrade intelligence (§11 "Upgrade moment" / §16.3 upgrade endpoint) ──
# Detecting the pattern and drafting the case never touches billing, so
# it's fully autonomous. Actually changing what a member is billed is
# the one financial-risk step — that always needs the member's own
# acceptance, the same principle as HRM's benefits-cost-increase gate,
# just member-facing instead of employee-facing.
class UpgradeCaseStatus(str, enum.Enum):
    OFFERED = "offered"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class AboveClassRequest(Base):
    """A raw signal: the member requested/booked a vehicle above their
    current subscribed class. Individually meaningless; the pattern
    across several is what the agent reasons about."""

    __tablename__ = "above_class_requests"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    subscribed_class: Mapped[MobilityClass] = mapped_column(Enum(MobilityClass), nullable=False)
    requested_class: Mapped[MobilityClass] = mapped_column(Enum(MobilityClass), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UpgradeCase(Base):
    __tablename__ = "upgrade_cases"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    current_class: Mapped[MobilityClass] = mapped_column(Enum(MobilityClass), nullable=False)
    recommended_class: Mapped[MobilityClass] = mapped_column(Enum(MobilityClass), nullable=False)
    above_class_request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[UpgradeCaseStatus] = mapped_column(Enum(UpgradeCaseStatus), nullable=False, default=UpgradeCaseStatus.OFFERED)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PauseHistoryEntry(Base):
    """Real historical pause tracking — Subscription itself only ever
    holds the current pause state (paused_at/resume_at get cleared on
    resume), so there was no way to answer "how many times has this
    member paused in the last year" from that alone. This table exists
    specifically to make that a real, queryable number for churn-risk
    context rather than a stand-in value."""

    __tablename__ = "pause_history_entries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    paused_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
