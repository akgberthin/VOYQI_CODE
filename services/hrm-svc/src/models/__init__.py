"""
HRM-001 Employee Directory & HR Core.

Design principle for this whole module, stated once: onboarding and
offboarding checklist ITEMS are a genuine judgment call ("given this
role and entity, which tasks/access/equipment apply") — that's the
Talent Agent's job, evaluated automatically the moment an employee
record is created or an offboarding is initiated. Whether an item is
actually DONE is a fact, never an agent's guess — the checklist
completion gate is a hard, deterministic check identical in shape to
dms-svc's ReconOrder and hub-svc's prep checklist. Compensation
history, skills, certifications, and the document vault are all facts
ingested or recorded directly — nothing here is AI-authored.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class EmploymentStatus(str, enum.Enum):
    ACTIVE = "active"
    ON_LEAVE = "on_leave"
    TERMINATED = "terminated"


class EmployeeProfile(Base):
    """The employee master record. entity_ref points at the
    dealership rooftop, hub, or corporate entity this employee belongs
    to by convention (a cross-service reference, no shared database —
    the same pattern used for VINs, lender refs, and every other
    cross-service id on this platform). manager_id is this employee's
    primary reporting line; MatrixReportingLink covers secondary/dotted
    lines separately."""

    __tablename__ = "employee_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    entity_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    job_title: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    department: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    manager_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)
    employment_status: Mapped[EmploymentStatus] = mapped_column(Enum(EmploymentStatus), nullable=False, default=EmploymentStatus.ACTIVE)
    hire_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    termination_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class MatrixReportingLink(Base):
    """A secondary/dotted-line reporting relationship, on top of
    EmployeeProfile.manager_id's primary line."""

    __tablename__ = "matrix_reporting_links"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    secondary_manager_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    context: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class Position(Base):
    """The job catalog — position management, pay grades, salary
    bands. vacancy tracking supported via is_vacant."""

    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    department: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    pay_grade: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    salary_band_min: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    salary_band_max: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_vacant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ChecklistKind(str, enum.Enum):
    ONBOARDING = "onboarding"
    OFFBOARDING = "offboarding"


class ChecklistStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"


class EmployeeChecklist(Base):
    """Covers both onboarding and offboarding — same shape, same hard
    gate. mark_complete refuses while any task is incomplete, exactly
    like dms-svc's ReconOrder and hub-svc's prep checklist."""

    __tablename__ = "employee_checklists"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    kind: Mapped[ChecklistKind] = mapped_column(Enum(ChecklistKind), nullable=False)
    status: Mapped[ChecklistStatus] = mapped_column(Enum(ChecklistStatus), nullable=False, default=ChecklistStatus.PENDING)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ChecklistTask(Base):
    """A single task on an onboarding/offboarding checklist. The
    Talent Agent recommends which tasks apply (given role/entity/
    department) — but this row existing and being checked off is
    always a deterministic fact, never a guess."""

    __tablename__ = "checklist_tasks"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    checklist_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DocumentType(str, enum.Enum):
    CONTRACT = "contract"
    NDA = "nda"
    TAX_FORM = "tax_form"
    CERTIFICATION = "certification"
    PERFORMANCE_REVIEW = "performance_review"
    ACKNOWLEDGMENT = "acknowledgment"


class DocumentVaultEntry(Base):
    """The digital document vault — a real record of what exists and
    where, not the binary content itself (matching how every other
    document-tracking table on this platform works)."""

    __tablename__ = "document_vault_entries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    document_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType), nullable=False)
    file_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SkillRecord(Base):
    __tablename__ = "skill_records"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False)
    proficiency_level: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class CertificationRecord(Base):
    __tablename__ = "certification_records"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmergencyContact(Base):
    __tablename__ = "emergency_contacts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    relationship: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")


class CompensationReason(str, enum.Enum):
    HIRE = "hire"
    MERIT = "merit"
    PROMOTION = "promotion"
    ADJUSTMENT = "adjustment"


class CompensationRecord(Base):
    """Compensation history — a fact, recorded exactly as it happened,
    never AI-authored or AI-decided."""

    __tablename__ = "compensation_records"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    effective_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    annual_salary_usd: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[CompensationReason] = mapped_column(Enum(CompensationReason), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")


class PayRunStatus(str, enum.Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    COMPLETED = "completed"


class PayRun(Base):
    """HRM-002 — one payroll run scoped to a single legal entity
    (entity_ref, the same cross-service reference convention used
    throughout this platform). Every dollar figure here is computed by
    payroll_engine's deterministic formulas — there is no AI reasoning
    anywhere in this module's math. A miscalculated paycheck is real
    financial harm to a real person, not a cosmetic bug."""

    __tablename__ = "pay_runs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_ref: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pay_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pay_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[PayRunStatus] = mapped_column(Enum(PayRunStatus), nullable=False, default=PayRunStatus.DRAFT)
    total_gross_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_net_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_tax_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class PayStub(Base):
    """One employee's line within a pay run — every field here is a
    deterministic calculation, logged in full so it's auditable exactly
    the way a real paycheck must be."""

    __tablename__ = "pay_stubs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    pay_run_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    gross_pay_usd: Mapped[float] = mapped_column(Float, nullable=False)
    fica_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    federal_tax_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    state_tax_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    garnishment_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    benefits_premium_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    net_pay_usd: Mapped[float] = mapped_column(Float, nullable=False)


class GarnishmentType(str, enum.Enum):
    CHILD_SUPPORT = "child_support"
    TAX_LEVY = "tax_levy"
    CREDITOR_GARNISHMENT = "creditor_garnishment"
    STUDENT_LOAN = "student_loan"


class GarnishmentOrder(Base):
    """A legal order employers must comply with exactly as issued —
    applying it is a deterministic pass-through, never a judgment
    call."""

    __tablename__ = "garnishment_orders"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    order_type: Mapped[GarnishmentType] = mapped_column(Enum(GarnishmentType), nullable=False)
    amount_per_period_usd: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BenefitsPlanType(str, enum.Enum):
    MEDICAL = "medical"
    DENTAL = "dental"
    VISION = "vision"
    RETIREMENT = "retirement"
    LIFE_INSURANCE = "life_insurance"


class BenefitsTier(str, enum.Enum):
    EMPLOYEE_ONLY = "employee_only"
    EMPLOYEE_SPOUSE = "employee_spouse"
    EMPLOYEE_CHILDREN = "employee_children"
    FAMILY = "family"


class BenefitsPlan(Base):
    __tablename__ = "benefits_plans"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_type: Mapped[BenefitsPlanType] = mapped_column(Enum(BenefitsPlanType), nullable=False)
    tier: Mapped[BenefitsTier] = mapped_column(Enum(BenefitsTier), nullable=False)
    monthly_premium_usd: Mapped[float] = mapped_column(Float, nullable=False)
    employer_contribution_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class EnrollmentStatus(str, enum.Enum):
    ACTIVE = "active"
    PENDING = "pending"
    TERMINATED = "terminated"


class BenefitsEnrollment(Base):
    __tablename__ = "benefits_enrollments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    status: Mapped[EnrollmentStatus] = mapped_column(Enum(EnrollmentStatus), nullable=False, default=EnrollmentStatus.PENDING)
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class PayrollTaxStatus(Base):
    """Multi-state payroll tax status tracking — a fact recorded per
    employee, never inferred."""

    __tablename__ = "payroll_tax_statuses"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True, unique=True)
    work_state: Mapped[str] = mapped_column(String(2), nullable=False, default="FL")
    residence_state: Mapped[str] = mapped_column(String(2), nullable=False, default="FL")
    filing_status: Mapped[str] = mapped_column(String(32), nullable=False, default="single")
    is_multi_state: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BenefitsRecommendationStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class BenefitsRecommendation(Base):
    """The Talent Agent's output — a suggestion, never an automatic
    enrollment. accept_benefits_recommendation in the engine is the
    sole human-confirmed action that actually creates a
    BenefitsEnrollment from one of these."""

    __tablename__ = "benefits_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    recommended_plan_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[BenefitsRecommendationStatus] = mapped_column(Enum(BenefitsRecommendationStatus), nullable=False, default=BenefitsRecommendationStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SourceChannel(str, enum.Enum):
    REFERRAL = "referral"
    JOB_BOARD = "job_board"
    UNIVERSITY = "university"
    AGENCY = "agency"
    DIRECT = "direct"


class CandidateProfile(Base):
    """HRM-003 — mirrors crm-svc's PersonProfile/Customer 360 pattern
    architecturally (candidates managed with the same relationship-
    graph discipline as customers), but deliberately stays its own
    identity graph. Candidates and customers have different consent
    regimes and different legal protections — this is never merged
    into crm-svc's PersonProfile table, even though the pattern is the
    same."""

    __tablename__ = "candidate_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_channel: Mapped[SourceChannel] = mapped_column(Enum(SourceChannel), nullable=False, default=SourceChannel.DIRECT)
    resume_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class RequisitionStatus(str, enum.Enum):
    OPEN = "open"
    ON_HOLD = "on_hold"
    FILLED = "filled"
    CANCELLED = "cancelled"


class Requisition(Base):
    """A job requisition, optionally tied to a Position from HRM-001's
    job catalog. required_skills is a plain comma-separated list — the
    deterministic keyword-overlap fallback's real input, not a
    placeholder."""

    __tablename__ = "requisitions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    position_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    department: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    required_skills: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[RequisitionStatus] = mapped_column(Enum(RequisitionStatus), nullable=False, default=RequisitionStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ApplicationStage(str, enum.Enum):
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW = "interview"
    OFFER = "offer"
    HIRED = "hired"
    REJECTED = "rejected"


class Application(Base):
    """The requisition-to-offer pipeline entry. score/score_reasoning
    are the Talent Agent's resume-screening output — a signal for a
    recruiter, never an automatic reject. Stage advancement past
    SCREENING is always a human call: a real interview outcome is
    something no scoring heuristic observes."""

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    requisition_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    stage: Mapped[ApplicationStage] = mapped_column(Enum(ApplicationStage), nullable=False, default=ApplicationStage.APPLIED)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CandidateInteractionType(str, enum.Enum):
    APPLICATION_SUBMITTED = "application_submitted"
    SCREENING_CALL = "screening_call"
    INTERVIEW = "interview"
    OFFER_EXTENDED = "offer_extended"
    REJECTION = "rejection"
    NURTURE_MESSAGE = "nurture_message"


class CandidateInteraction(Base):
    """The candidate relationship timeline — a fact, ingested or
    recorded directly, never AI-authored. Mirrors CRM-001's
    InteractionEvent exactly."""

    __tablename__ = "candidate_interactions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    interaction_type: Mapped[CandidateInteractionType] = mapped_column(Enum(CandidateInteractionType), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class NurtureDraftStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED_SENT = "approved_sent"
    REJECTED = "rejected"


class CandidateNurtureDraft(Base):
    """Mirrors marketing-svc's ContentDraft and crm-svc's DraftReply
    exactly: the agent that writes this row never sets status past
    PENDING_REVIEW. There is no tool the Talent Agent has that can
    message a candidate directly."""

    __tablename__ = "candidate_nurture_drafts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    draft_body: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[NurtureDraftStatus] = mapped_column(Enum(NurtureDraftStatus), nullable=False, default=NurtureDraftStatus.PENDING_REVIEW)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class GoalStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    MISSED = "missed"


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[GoalStatus] = mapped_column(Enum(GoalStatus), nullable=False, default=GoalStatus.NOT_STARTED)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ReviewCycleStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class ReviewCycle(Base):
    __tablename__ = "review_cycles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ReviewCycleStatus] = mapped_column(Enum(ReviewCycleStatus), nullable=False, default=ReviewCycleStatus.OPEN)


class ReviewStatus(str, enum.Enum):
    DRAFT = "draft"
    FINALIZED = "finalized"


class PerformanceReview(Base):
    """rating and manager_notes are a human's real judgment, recorded
    exactly as entered — never AI-set. manager_notes is the raw input
    the Talent Agent drafts polished narrative feedback from; the
    rating itself is never touched by the agent."""

    __tablename__ = "performance_reviews"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    review_cycle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    manager_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    finalized_feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[ReviewStatus] = mapped_column(Enum(ReviewStatus), nullable=False, default=ReviewStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class FeedbackDraftStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED_FINALIZED = "approved_finalized"
    REJECTED = "rejected"


class FeedbackDraft(Base):
    """Mirrors every other draft pattern on this platform exactly: the
    agent that writes this row never sets status past PENDING_REVIEW.
    The manager always reviews/edits before the feedback is finalized
    onto the review record."""

    __tablename__ = "feedback_drafts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    review_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    draft_body: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[FeedbackDraftStatus] = mapped_column(Enum(FeedbackDraftStatus), nullable=False, default=FeedbackDraftStatus.PENDING_REVIEW)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class BoxLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NineBoxStatus(str, enum.Enum):
    AI_SUGGESTED = "ai_suggested"
    CONFIRMED = "confirmed"


class NineBoxPlacement(Base):
    """The 9-box grid — this platform's succession-planning tool: the
    Talent Agent recommends a performance x potential placement, a
    human (typically in a calibration session) confirms it. However
    confident the suggestion, placement is never final until
    confirmed."""

    __tablename__ = "nine_box_placements"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    review_cycle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    performance_axis: Mapped[BoxLevel] = mapped_column(Enum(BoxLevel), nullable=False)
    potential_axis: Mapped[BoxLevel] = mapped_column(Enum(BoxLevel), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[NineBoxStatus] = mapped_column(Enum(NineBoxStatus), nullable=False, default=NineBoxStatus.AI_SUGGESTED)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AttritionRiskScore(Base):
    """The Talent Agent's output — a deterministic, fully auditable
    heuristic is the floor (mirrors the Member Experience Agent's
    churn-scoring discipline exactly), never a black box. A signal for
    HR to act on, never an automatic decision about anyone's
    employment."""

    __tablename__ = "attrition_risk_scores"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CourseCategory(str, enum.Enum):
    COMPLIANCE = "compliance"
    SAFETY = "safety"
    TECHNICAL = "technical"
    LEADERSHIP = "leadership"


class CourseContentType(str, enum.Enum):
    VIDEO = "video"
    DOCUMENT = "document"
    QUIZ = "quiz"
    IN_PERSON = "in_person"
    OTHER = "other"


class Course(Base):
    """FLT-style catalog entry for VOYQI Academy — a configured fact,
    not AI-generated. is_mandatory + category drive the deterministic
    recommendation priority (compliance and safety courses always
    outrank optional development courses). content_type/content_url
    make this a real library — a course entry can point at a video,
    a document, a quiz, or an in-person session, not just a title."""

    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[CourseCategory] = mapped_column(Enum(CourseCategory), nullable=False)
    duration_hours: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    department: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    content_type: Mapped[CourseContentType] = mapped_column(Enum(CourseContentType), nullable=False, default=CourseContentType.DOCUMENT)
    content_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class CourseCompletionStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"


class CourseCompletion(Base):
    __tablename__ = "course_completions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    course_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    status: Mapped[CourseCompletionStatus] = mapped_column(Enum(CourseCompletionStatus), nullable=False, default=CourseCompletionStatus.IN_PROGRESS)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CovenantAcknowledgment(Base):
    """A signed compliance record — a deterministic fact, the same
    discipline as a consent flag. Never AI-generated or AI-signed."""

    __tablename__ = "covenant_acknowledgments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    covenant_version: Mapped[str] = mapped_column(String(32), nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CourseRecommendationStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CourseRecommendation(Base):
    """The Talent Agent's output — always PENDING when written, the
    same draft-then-human-confirms shape as every other recommendation
    on this platform. An employee (or their manager) accepting it is
    what actually enrolls them; the agent never enrolls anyone itself."""

    __tablename__ = "course_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    recommended_course_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[CourseRecommendationStatus] = mapped_column(Enum(CourseRecommendationStatus), nullable=False, default=CourseRecommendationStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CaseType(str, enum.Enum):
    PAYROLL_INQUIRY = "payroll_inquiry"
    BENEFITS_QUESTION = "benefits_question"
    POLICY_QUESTION = "policy_question"
    COMPLAINT = "complaint"
    DOCUMENT_REQUEST = "document_request"


class CaseStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class HRCase(Base):
    """SLA tracking is plain date arithmetic, the same principle as
    ORG-001's SLA pattern. sla_due_at is set deterministically at
    creation based on case_type — never AI-estimated."""

    __tablename__ = "hr_cases"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    case_type: Mapped[CaseType] = mapped_column(Enum(CaseType), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[CaseStatus] = mapped_column(Enum(CaseStatus), nullable=False, default=CaseStatus.OPEN)
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CaseMessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CaseMessage(Base):
    """The case conversation timeline — a fact, ingested or recorded
    directly, never AI-authored. Mirrors CRM-003's Message pattern
    exactly."""

    __tablename__ = "case_messages"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    direction: Mapped[CaseMessageDirection] = mapped_column(Enum(CaseMessageDirection), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sender: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class PolicyDocument(Base):
    """The source-of-truth policy content the Talent Agent's Q&A
    capability grounds its answers in — a configured fact, never
    AI-generated. The agent can only cite what's actually here."""

    __tablename__ = "policy_documents"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")


class CaseDraftReplyStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED_SENT = "approved_sent"
    REJECTED = "rejected"


class CaseDraftReply(Base):
    """Mirrors every other draft pattern on this platform exactly —
    the agent that writes this row never sets status past
    PENDING_REVIEW. cites_policy_id is nullable: a draft with no
    grounding policy is a signal HR should write this one by hand."""

    __tablename__ = "case_draft_replies"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    draft_body: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cites_policy_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    status: Mapped[CaseDraftReplyStatus] = mapped_column(Enum(CaseDraftReplyStatus), nullable=False, default=CaseDraftReplyStatus.PENDING_REVIEW)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class NoticeChannel(str, enum.Enum):
    APP = "app"
    EMAIL = "email"
    SMS = "sms"


class Notice(Base):
    """A company-wide notice/announcement. channels records the
    INTENDED delivery channels as a plain fact — this platform has no
    integrated SMS/email gateway (that would require a real Twilio/
    SendGrid-style integration this codebase doesn't have), so email
    and sms here mean 'flagged for that channel', not 'actually
    delivered'. app delivery is the one channel this platform can
    genuinely fulfill on its own, via the notice board itself."""

    __tablename__ = "notices"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    posted_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    channels: Mapped[str] = mapped_column(String(64), nullable=False, default="app")  # comma-separated NoticeChannel values
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class NoticeComment(Base):
    """The notice board's discussion thread — a fact, ingested or
    recorded directly, never AI-authored. Mirrors CaseMessage and
    CandidateInteraction exactly."""

    __tablename__ = "notice_comments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    notice_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class EmployeeNote(Base):
    """Internal documentation on an employee's record — human or
    AI-authored. Unlike every consequential draft elsewhere on this
    platform, notes don't require human approval before they're
    written: a note is additive record-keeping, not a decision that
    affects anyone's pay, benefits, or employment. The one hard rule
    that still applies is append-only — see append_to_note in
    note_engine.py — an AI update is never allowed to overwrite or
    delete a human's original words, only add to them with clear
    attribution."""

    __tablename__ = "employee_notes"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class EmailRecipientScope(str, enum.Enum):
    ALL_STAFF = "all_staff"
    SINGLE_EMPLOYEE = "single_employee"


class EmailStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"


class EmailCommunication(Base):
    """Formal email — bulk (all staff) or single-employee. Mirrors
    every other draft pattern on this platform: whether the body was
    written by a human or by the Talent Agent from a short prompt,
    it's always created in DRAFT status first. send_email in
    email_engine.py is the sole action that actually dispatches it —
    there is no path, AI or manual, that skips that human confirmation
    step. This platform has no integrated email/SMTP gateway (that
    would need a real SendGrid-style integration), so "sent" here
    means the administrator confirmed dispatch — the actual delivery
    mechanics are outside this codebase's scope, the same honesty
    applied to Notice's email/sms channels."""

    __tablename__ = "email_communications"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    recipient_scope: Mapped[EmailRecipientScope] = mapped_column(Enum(EmailRecipientScope), nullable=False)
    recipient_employee_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    ai_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[EmailStatus] = mapped_column(Enum(EmailStatus), nullable=False, default=EmailStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CourseDraftStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    REJECTED = "rejected"


class CourseDraft(Base):
    """AI-assisted course prep — the Talent Agent drafts a title,
    category, description, and suggested content type from a short
    HR prompt. Mirrors every other draft pattern on this platform:
    always PENDING_REVIEW when written, never directly added to the
    live catalog. publish_course_draft in training_engine.py is the
    sole human-confirmed action that turns a draft into a real Course
    employees can be assigned to."""

    __tablename__ = "course_drafts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    draft_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    draft_category: Mapped[CourseCategory] = mapped_column(Enum(CourseCategory), nullable=False, default=CourseCategory.TECHNICAL)
    draft_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    draft_content_type: Mapped[CourseContentType] = mapped_column(Enum(CourseContentType), nullable=False, default=CourseContentType.DOCUMENT)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[CourseDraftStatus] = mapped_column(Enum(CourseDraftStatus), nullable=False, default=CourseDraftStatus.PENDING_REVIEW)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
