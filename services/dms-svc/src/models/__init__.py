"""
dms-svc data model — Dealer Management System backend for VOYQI Auto
Group, covering all 13 DMS capability areas (rooftops, retail
inventory, sales desk, F&I/compliance, lending, trade-in appraisal,
recon, service, parts, loaners, franchise compliance, P&L, and the
security-hold compliance queue).

Governing principle across every table here: full automation by
default. The Dealer Intelligence Agent (and the Pricing Intelligence
Agent, which already covers trade appraisal and retail pricing) write
directly to the fields that represent business decisions — price,
approval status, lender routing, dispatch, reorder — with no pending/
approval state in between. The only fields any agent has no path to
at all are the three genuine security matters: OFAC sanctions status,
Red-Flags-Rule identity-theft overrides, and stolen-vehicle registry
holds. Everything else that might look like a "should stay manual"
candidate — title disclosure, adverse-action deadlines, deal margin
approval, franchise remediation — is intentionally NOT gated; that was
an explicit platform decision, not an oversight.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, GUID


# ══════════════════════════════════════════════════════════════════
# DMS-001 — Rooftop Directory & Entity Mapping
# ══════════════════════════════════════════════════════════════════

class Rooftop(Base):
    """A single-brand OEM franchise location. Each rooftop holds
    exactly one brand's franchise agreement — this is a real legal
    constraint, not a data-model simplification, and it's why cross-
    rooftop inventory transfer is never a valid Dealer Intelligence
    Agent action: a vehicle can only ever be sold at the one rooftop
    licensed to sell its brand. renewal_filed is set autonomously by
    the agent the moment the license is within the renewal window —
    a real administrative action, not just a dashboard flag."""

    __tablename__ = "rooftops"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    holding_company: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    oem_brand: Mapped[str] = mapped_column(String(64), nullable=False)
    license_number: Mapped[str] = mapped_column(String(64), nullable=False)
    license_expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    renewal_filed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# DMS-002 — Retail Inventory & Merchandising
# ══════════════════════════════════════════════════════════════════

class VehicleCondition(str, enum.Enum):
    NEW = "new"
    USED = "used"


class VehicleStatus(str, enum.Enum):
    IN_STOCK = "in_stock"
    PENDING_SALE = "pending_sale"
    IN_RECON = "in_recon"
    SOLD = "sold"


class Vehicle(Base):
    """Retail inventory — one row per unit on a rooftop's lot.
    price_usd is written directly by the Pricing Intelligence Agent's
    aging-markdown sweep and by any manual re-price call; there is no
    separate 'recommended price' field awaiting a click, because a
    recommendation that never lands anywhere is worse than no feature
    at all. last_markdown_days guards the sweep against re-applying a
    markdown at the same aging value on repeated evaluation."""

    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rooftop_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("rooftops.id"), nullable=False)
    vin: Mapped[str] = mapped_column(String(17), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    condition: Mapped[VehicleCondition] = mapped_column(Enum(VehicleCondition), nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    days_in_inventory: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[VehicleStatus] = mapped_column(Enum(VehicleStatus), nullable=False, default=VehicleStatus.IN_STOCK)
    last_markdown_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pre_markdown_price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BookValueSource(str, enum.Enum):
    KBB = "kbb"
    BLACK_BOOK = "black_book"
    JD_POWER = "jd_power"
    MANHEIM_MMR = "manheim_mmr"


class BookValueQuote(Base):
    """Real third-party book-value data on file for a VIN — this is
    what makes retail pricing genuinely different from trade-in
    valuation: real market data exists to read here."""

    __tablename__ = "book_value_quotes"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vin: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    source: Mapped[BookValueSource] = mapped_column(Enum(BookValueSource), nullable=False)
    wholesale_usd: Mapped[float] = mapped_column(Float, nullable=False)
    retail_usd: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TitleStatus(str, enum.Enum):
    CLEAN = "clean"
    SALVAGE = "salvage"
    FLOOD = "flood"
    LEMON = "lemon"
    REBUILT = "rebuilt"


class VehicleHistoryReport(Base):
    """A CarFax/AutoCheck pull for a vehicle already in inventory.
    Title-brand disclosure is a consumer-protection/legal obligation,
    not a security matter — the Dealer Intelligence Agent drafts the
    disclosure text and syncs the listing autonomously; there is no
    human-typed-override gate on this, unlike the three genuine
    security fields elsewhere in this schema."""

    __tablename__ = "vehicle_history_reports"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vin: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    title_status: Mapped[TitleStatus] = mapped_column(Enum(TitleStatus), nullable=False)
    accident_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    odometer_rollback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disclosure_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    listing_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PricingRecommendation(Base):
    """Audit trail of every autonomous price the agent has ever
    applied to a vehicle — not a queue of recommendations awaiting
    action, since the price is already applied by the time this row
    exists."""

    __tablename__ = "pricing_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("vehicles.id"), nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# DMS-003 — Sales Desk & Deal Manager
# ══════════════════════════════════════════════════════════════════

class DealStatus(str, enum.Enum):
    WORKING = "working"
    DESKING = "desking"
    APPROVED = "approved"
    DELIVERED = "delivered"
    UNWOUND = "unwound"


class Deal(Base):
    """Desking and approval both execute autonomously in the Deal
    Desk Intelligence Agent's single action: a price is computed, and
    if it misses the minimum gross floor, it's auto-countered upward
    and approved in the same pass — never silently approved below
    floor, and never queued for a manager to look at."""

    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rooftop_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("rooftops.id"), nullable=False)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("vehicles.id"), nullable=False)
    buyer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[DealStatus] = mapped_column(Enum(DealStatus), nullable=False, default=DealStatus.WORKING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# DMS-004 — F&I Menu & Compliance Console, Lender Access
# ══════════════════════════════════════════════════════════════════

class Lender(Base):
    __tablename__ = "lenders"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class CreditApplicationStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    BLOCKED = "blocked"
    DECISIONED = "decisioned"


class CreditApplication(Base):
    """The ONE security field on this table is ofac_match: a True
    value permanently sets status to BLOCKED with no code path back
    to SUBMITTED or DECISIONED — this is enforced structurally in
    lending_engine.py, not just by convention. Every other field here
    (bureau, fico, submissions, selected_submission_id) is written
    autonomously by the Lending Intelligence Agent: routing, per-
    lender decisioning, and winner selection all happen in one chain
    triggered by the bureau pull clearing."""

    __tablename__ = "credit_applications"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deal_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("deals.id"), nullable=False)
    applicant_name: Mapped[str] = mapped_column(String(128), nullable=False)
    annual_income_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[CreditApplicationStatus] = mapped_column(Enum(CreditApplicationStatus), nullable=False, default=CreditApplicationStatus.DRAFT)
    bureau: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fico: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ofac_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selected_submission_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SubmissionStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    COUNTERED = "countered"
    DECLINED = "declined"


class LenderSubmission(Base):
    """One row per lender a credit application was auto-routed to.
    status/apr/term/amount/reserve are all written by
    autoDecideSubmission's deterministic FICO-tier policy — the same
    policy a live-Claude path would reason over with more signals, but
    the no-key fallback always produces a real decision, never a
    permanently-pending row."""

    __tablename__ = "lender_submissions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    credit_application_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("credit_applications.id"), nullable=False)
    lender_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("lenders.id"), nullable=False)
    status: Mapped[SubmissionStatus] = mapped_column(Enum(SubmissionStatus), nullable=False, default=SubmissionStatus.SUBMITTED)
    apr: Mapped[float | None] = mapped_column(Float, nullable=True)
    term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    reserve_usd: Mapped[float | None] = mapped_column(Float, nullable=True)


class AdverseActionStatus(str, enum.Enum):
    ISSUED = "issued"


class AdverseActionNotice(Base):
    """ECOA/Reg B adverse-action notices are a legal deadline, not a
    judgment call — the Compliance Intelligence Agent auto-issues one
    the instant a decline is recorded. status only ever has one value
    (ISSUED) because nothing in the autonomous path ever leaves one
    pending; a 'required'/'overdue' distinction only existed for
    legacy backlog predating full automation."""

    __tablename__ = "adverse_action_notices"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deal_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("deals.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AdverseActionStatus] = mapped_column(Enum(AdverseActionStatus), nullable=False, default=AdverseActionStatus.ISSUED)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RedFlagCheck(Base):
    """The second of the three genuine security fields on this
    platform: identity-theft indicators under the FTC Red Flags Rule.
    overridden_by/override_reason are the ONLY fields on this entire
    schema that require a human-typed justification before a value
    can be written — every other override-shaped field elsewhere
    (title disclosure, covenant-adjacent items) was deliberately
    automated away. This one stays, because identity theft prevention
    is a security matter, not a business judgment call."""

    __tablename__ = "red_flag_checks"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deal_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("deals.id"), nullable=False)
    flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    overridden_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


# ══════════════════════════════════════════════════════════════════
# DMS-005 — Trade-in & Appraisal Console
# ══════════════════════════════════════════════════════════════════

class AppraisalStatus(str, enum.Enum):
    PENDING = "pending"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    SECURITY_HOLD = "security_hold"


class Appraisal(Base):
    """The third genuine security field on this platform lives here:
    a SECURITY_HOLD status (set when a VIN matches the stolen-vehicle
    registry) has no code path to OFFERED or ACCEPTED — acquiring a
    stolen vehicle is a law-enforcement matter, not a pricing
    decision, and this mirrors OFAC's no-override design exactly.
    Every other transition (valuation, offer) is fully autonomous:
    the Pricing Intelligence Agent's evaluate_trade_appraisal writes
    valuation_usd AND offer_usd in the same call, no separate approval
    step in between."""

    __tablename__ = "appraisals"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vin: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    mileage: Mapped[int] = mapped_column(Integer, nullable=False)
    valuation_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    offer_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[AppraisalStatus] = mapped_column(Enum(AppraisalStatus), nullable=False, default=AppraisalStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# DMS-006 (extension) — Recon Workflow
# ══════════════════════════════════════════════════════════════════

class ReconOrderStatus(str, enum.Enum):
    AWAITING_INSPECTION = "awaiting_inspection"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ReconOrder(Base):
    """The aging clock in Retail Inventory resets here, not at
    physical arrival. The order auto-advances to COMPLETED the instant
    every work item is marked done — the Recon Intelligence Agent
    doesn't wait for a manager to click 'front-line ready'. Marking an
    individual item done stays a direct, ungated action: it's a
    technician reporting real physical completion, not a business
    decision the agent should own."""

    __tablename__ = "recon_orders"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("vehicles.id"), nullable=False)
    arrived_days_ago: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ReconOrderStatus] = mapped_column(Enum(ReconOrderStatus), nullable=False, default=ReconOrderStatus.AWAITING_INSPECTION)
    turnaround_days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReconWorkItemCategory(str, enum.Enum):
    MECHANICAL = "mechanical"
    DETAIL = "detail"
    BODY = "body"
    TIRES = "tires"
    OTHER = "other"


class ReconWorkItemStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class ReconWorkItem(Base):
    """assigned_to is set autonomously by category at creation time —
    never 'unassigned' waiting on a human dispatcher."""

    __tablename__ = "recon_work_items"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    recon_order_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("recon_orders.id"), nullable=False)
    category: Mapped[ReconWorkItemCategory] = mapped_column(Enum(ReconWorkItemCategory), nullable=False)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    assigned_to: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ReconWorkItemStatus] = mapped_column(Enum(ReconWorkItemStatus), nullable=False, default=ReconWorkItemStatus.PENDING)


# ══════════════════════════════════════════════════════════════════
# DMS-006 — Service & Repair Orders
# ══════════════════════════════════════════════════════════════════

class RepairOrderStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    WARRANTY_CLAIM = "warranty_claim"
    COMPLETED = "completed"


class RepairOrder(Base):
    """The Service Intelligence Agent auto-dispatches a tech and
    estimates duration the instant an RO is opened — tech/status are
    never left at an 'open, awaiting dispatch' state. estimated
    duration_days, drawn from job-description keywords, is what
    triggers an autonomous loaner assignment in loaner_engine.py when
    it's a day or more."""

    __tablename__ = "repair_orders"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rooftop_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("rooftops.id"), nullable=False)
    vin: Mapped[str] = mapped_column(String(17), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    tech: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    warranty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revenue_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[RepairOrderStatus] = mapped_column(Enum(RepairOrderStatus), nullable=False, default=RepairOrderStatus.IN_PROGRESS)
    estimated_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# DMS-007 — Parts Inventory & Vendor Ordering
# ══════════════════════════════════════════════════════════════════

class Part(Base):
    """The Parts Intelligence Agent auto-places a vendor order the
    instant stock is at or below reorder_point — last_reorder_stock_qty
    guards against re-ordering repeatedly at the same stock level on
    repeated evaluation, the same pattern as Vehicle's
    last_markdown_days above."""

    __tablename__ = "parts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    part_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    unit_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    stock_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    last_reorder_stock_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ══════════════════════════════════════════════════════════════════
# DMS-008 — Loaner & Courtesy Vehicle Manager
# ══════════════════════════════════════════════════════════════════

class LoanerStatus(str, enum.Enum):
    OUT = "out"
    RETURNED = "returned"


class Loaner(Base):
    """Auto-assigned by loaner_engine.py the instant a repair order's
    estimated duration crosses the threshold — chained directly from
    RepairOrder dispatch, not a separate reservation step. billing_flag
    auto-sets when days_out crosses the extended-loaner threshold."""

    __tablename__ = "loaners"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    repair_order_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("repair_orders.id"), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    vehicle_label: Mapped[str] = mapped_column(String(128), nullable=False)
    days_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[LoanerStatus] = mapped_column(Enum(LoanerStatus), nullable=False, default=LoanerStatus.OUT)
    billing_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ══════════════════════════════════════════════════════════════════
# DMS-010 — OEM & Franchise Compliance
# ══════════════════════════════════════════════════════════════════

class FranchiseStatus(str, enum.Enum):
    COMPLIANT = "compliant"
    AT_RISK = "at_risk"
    NON_COMPLIANT = "non_compliant"


class FranchiseCompliance(Base):
    """remediation_opened is set autonomously the instant status
    crosses into NON_COMPLIANT — the Franchise Intelligence Agent
    doesn't wait for someone to notice a red pill on a dashboard."""

    __tablename__ = "franchise_compliance"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rooftop_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("rooftops.id"), nullable=False)
    status: Mapped[FranchiseStatus] = mapped_column(Enum(FranchiseStatus), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    coop_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rebrand_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_applicable")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remediation_opened: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ══════════════════════════════════════════════════════════════════
# DMS-009 — Dealership P&L & KPI Board (capstone signal source)
# ══════════════════════════════════════════════════════════════════

class PLSnapshot(Base):
    """Every figure here is real transaction arithmetic — no AI
    reasoning belongs on a financial statement, full stop. This table
    exists purely as the read-only signal source the Dealer
    Performance Intelligence Agent (DMS-009 capstone) pulls from; the
    agent never writes to this table, only reads it, then acts
    elsewhere (see PerformanceEvaluation)."""

    __tablename__ = "pl_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rooftop_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("rooftops.id"), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_usd: Mapped[float] = mapped_column(Float, nullable=False)
    turn_rate: Mapped[float] = mapped_column(Float, nullable=False)
    fi_pvr_usd: Mapped[float] = mapped_column(Float, nullable=False)
    absorption_pct: Mapped[float] = mapped_column(Float, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PerformanceVerdict(str, enum.Enum):
    STRONG = "strong"
    MONITOR = "monitor"
    INTERVENE = "intervene"


class PerformanceEvaluation(Base):
    """The capstone's own output — a per-rooftop verdict, computed
    from PLSnapshot + FranchiseCompliance signals, that autonomously
    triggers an accelerated inventory markdown sweep (see
    performance_engine.py) when verdicted INTERVENE.
    intervention_applied guards against re-triggering the sweep on
    every re-evaluation."""

    __tablename__ = "performance_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rooftop_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("rooftops.id"), nullable=False)
    verdict: Mapped[PerformanceVerdict] = mapped_column(Enum(PerformanceVerdict), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    intervention_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Security-hold compliance queue — by design, the ONLY items that
# ever persist here are the three genuine security matters. Every
# other compliance event (title disclosure, franchise remediation,
# license renewal, adverse-action issuance) is an audit-log entry of
# an already-completed autonomous action, published as a Kafka event
# rather than written to this table at all.
# ══════════════════════════════════════════════════════════════════

class ComplianceHoldType(str, enum.Enum):
    OFAC = "ofac"
    RED_FLAG = "red_flag"
    STOLEN_VEHICLE = "stolen_vehicle"


class ComplianceHold(Base):
    """A row here means a human must act — there is no autonomous
    resolution path for any of these three types, structurally."""

    __tablename__ = "compliance_holds"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    type: Mapped[ComplianceHoldType] = mapped_column(Enum(ComplianceHoldType), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
