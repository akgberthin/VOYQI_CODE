"""
fleet-svc domain models.

VOYQI Fleet owns every subscription vehicle: the dealership sells, the
rental desk rents, the fleet owns. Vehicle is the one record every other
screen in this service and several others (Mobility, Rental) ultimately
reference by VIN.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class VehicleClass(str, enum.Enum):
    URBAN = "urban"
    EXECUTIVE = "executive"
    LUXURY = "luxury"
    LUXURY_PLUS = "luxury_plus"
    PERFORMANCE = "performance"
    PERFORMANCE_PLUS = "performance_plus"


class VehicleStatus(str, enum.Enum):
    STAGED = "staged"
    ASSIGNED = "assigned"
    IN_SERVICE = "in_service"
    IN_TRANSIT = "in_transit"
    MAINTENANCE = "maintenance"
    REMARKETING = "remarketing"
    RENTAL_POOL = "rental_pool"
    DISPOSED = "disposed"


class Vehicle(Base):
    """FLT-001 — VIN-level registry. Every fleet vehicle, everywhere,
    exactly one row. class assignment, status, current hub, odometer,
    and ownership entity are queried constantly by Fleet, Mobility, and
    Rental — this is the one source of truth all three read from.
    held_for_review and flagged_for_investigation are set autonomously
    by the Fleet Intelligence Agent (FLT-001 and FLT-002 respectively)
    and are real operational holds, not advisory annotations — a
    held/flagged vehicle is blocked from staging/assignment until a
    human clears the flag."""

    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vin: Mapped[str] = mapped_column(String(17), nullable=False, unique=True, index=True)
    make: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_class: Mapped[VehicleClass] = mapped_column(Enum(VehicleClass), nullable=False)

    status: Mapped[VehicleStatus] = mapped_column(
        Enum(VehicleStatus), nullable=False, default=VehicleStatus.STAGED
    )
    current_hub_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    odometer_miles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ownership_entity: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    is_provisioned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_class_certified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    held_for_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    held_for_review_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    flagged_for_investigation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class PurchaseOrderStatus(str, enum.Enum):
    ORDERED = "ordered"
    RECEIVED = "received"
    INSPECTED = "inspected"
    PROVISIONED = "provisioned"


class PurchaseOrder(Base):
    """FLT-002 — sourcing a vehicle before it becomes a Vehicle row.
    The PO -> received -> inspected -> provisioned chain has to complete
    in order; a vehicle can't be staged for members until
    is_class_certified is true on the resulting Vehicle."""

    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # dealer | auction
    vehicle_description: Mapped[str] = mapped_column(String(256), nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        Enum(PurchaseOrderStatus), nullable=False, default=PurchaseOrderStatus.ORDERED
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class IntakeInspection(Base):
    __tablename__ = "intake_inspections"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    checklist: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    inspected_by: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ProvisioningRecord(Base):
    """IoT device install, TLS client cert (VIN as Common Name — the
    platform's standing IoT security convention), and VOYQI Card pairing
    all have to be true before class certification can complete."""

    __tablename__ = "provisioning_records"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)

    iot_device_installed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tls_cert_common_name: Mapped[str] = mapped_column(String(17), nullable=False, default="")  # VIN as CN
    tls_cert_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    card_paired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    card_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    provisioned_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Hub(Base):
    """FLT-003 — a physical staging location. Vehicles' current_hub_id
    points here; demand/supply balancing happens hub by hub, class by
    class."""

    __tablename__ = "hubs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Where the hub is, so telemetry arriving over MQTT can be geofenced
    # server-side; a hub without coordinates is never geofenced.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    geofence_radius_miles: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class TransferOrderStatus(str, enum.Enum):
    PROPOSED = "proposed"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TransferOrder(Base):
    """A vehicle move from one hub to another. The Fleet Intelligence
    Agent's autonomous path (auto_dispatch_transfer) creates AND
    dispatches in one step — ai_initiated=True, dispatched_by=agent
    key. A human can still manually create/dispatch a PROPOSED order
    through the standalone propose_transfer/dispatch_transfer path."""

    __tablename__ = "transfer_orders"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    from_hub_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    to_hub_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)

    status: Mapped[TransferOrderStatus] = mapped_column(
        Enum(TransferOrderStatus), nullable=False, default=TransferOrderStatus.PROPOSED
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ai_initiated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    generated_by_agent_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    dispatched_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    driver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkOrderStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class MaintenanceAlertSeverity(str, enum.Enum):
    ROUTINE = "routine"
    PREDICTIVE = "predictive"
    URGENT = "urgent"


class MaintenanceAlert(Base):
    """FLT-004 — a predictive or routine maintenance signal. Generated
    deterministically (mileage-since-service threshold, or DTC codes
    present in a telemetry reading) — reliability matters more than
    cleverness for something that decides whether a member's vehicle
    gets pulled from active service."""

    __tablename__ = "maintenance_alerts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    severity: Mapped[MaintenanceAlertSeverity] = mapped_column(Enum(MaintenanceAlertSeverity), nullable=False)
    reason: Mapped[str] = mapped_column(String(256), nullable=False)
    dtc_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    alert_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    vendor: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[WorkOrderStatus] = mapped_column(Enum(WorkOrderStatus), nullable=False, default=WorkOrderStatus.OPEN)

    downtime_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    downtime_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    # Added for FLT-013 total-cost-of-ownership reporting — a real gap:
    # completed work orders previously had no cost recorded anywhere,
    # which made maintenance spend invisible to any TCO calculation.
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class IncidentType(str, enum.Enum):
    ACCIDENT = "accident"
    DAMAGE = "damage"
    THEFT = "theft"


class LiabilityAssessment(str, enum.Enum):
    MEMBER = "member"
    RENTER = "renter"
    THIRD_PARTY = "third_party"
    UNDETERMINED = "undetermined"


class IncidentStatus(str, enum.Enum):
    REPORTED = "reported"
    ASSESSED = "assessed"
    CLAIM_FILED = "claim_filed"
    REPAIRING = "repairing"
    CLOSED = "closed"
    TOTAL_LOSS = "total_loss"


class Incident(Base):
    """FLT-008 — accident/damage/theft intake. liability_assessment
    starts UNDETERMINED and is a human judgment call (assess_liability);
    declaring total loss is financially material enough that it always
    gets an audit-trail entry via the Compliance/Audit Agent, the same
    principle as any other high-materiality override elsewhere in the
    platform."""

    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    incident_type: Mapped[IncidentType] = mapped_column(Enum(IncidentType), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    photo_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reported_by: Mapped[str] = mapped_column(String(64), nullable=False)

    liability_assessment: Mapped[LiabilityAssessment] = mapped_column(
        Enum(LiabilityAssessment), nullable=False, default=LiabilityAssessment.UNDETERMINED
    )
    assessed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[IncidentStatus] = mapped_column(Enum(IncidentStatus), nullable=False, default=IncidentStatus.REPORTED)

    insurance_carrier: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    claim_number: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    total_loss_declared_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_loss_reason: Mapped[str] = mapped_column(String(256), nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class RepairOrder(Base):
    __tablename__ = "repair_orders"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    vendor: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[WorkOrderStatus] = mapped_column(Enum(WorkOrderStatus), nullable=False, default=WorkOrderStatus.OPEN)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class TelemetryReading(Base):
    """FLT-005 — per-VIN telemetry ingestion. DTC codes present here are
    exactly what maintenance_engine.evaluate_maintenance_need checks —
    telematics is the actual source of the 'predictive maintenance
    alerts from telematics' the spec calls for, not a separate system."""

    __tablename__ = "telemetry_readings"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    battery_or_fuel_pct: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    dtc_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    odometer_miles: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DeviceCertStatusEnum(str, enum.Enum):
    ACTIVE = "active"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"


class GeofenceAlert(Base):
    __tablename__ = "geofence_alerts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    hub_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)
    distance_miles: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class TamperEvent(Base):
    """Device tampering on a high-value fleet asset is a genuine
    security concern spanning Fleet and Security domains — this is a
    real Compliance/Audit Agent trigger, not a routine telemetry
    reading."""

    __tablename__ = "tamper_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    detected_signal: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DepreciationSchedule(Base):
    """FLT-006 — straight-line depreciation is the only method
    implemented; this is deliberately plain arithmetic, not a decision
    surface."""

    __tablename__ = "depreciation_schedules"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, unique=True, index=True)
    acquisition_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    salvage_value_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    useful_life_months: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class AssetValuation(Base):
    __tablename__ = "asset_valuations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    market_value_estimate_usd: Mapped[float] = mapped_column(Float, nullable=False)
    valued_by: Mapped[str] = mapped_column(String(64), nullable=False)
    valued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DispositionChannel(str, enum.Enum):
    """AUCTION/WHOLESALE/RETAIL_VOYQI_AUTO_GROUP are generic liquidation
    channels. RIDESHARE/LOCAL_RENTAL/EXPORT/PARTS are the actual
    documented depreciation cascade this platform is built around —
    a vehicle exiting subscription fleet duty moves through
    progressively lower-value uses rather than absorbing depreciation
    in one liquidation event, the strategy that sank prior competitors
    like Fair and Canvas. Before FLT-007's AI channel recommendation,
    this cascade was documented business strategy but never actually
    implemented in the channel options themselves."""

    AUCTION = "auction"
    WHOLESALE = "wholesale"
    RETAIL_VOYQI_AUTO_GROUP = "retail_voyqi_auto_group"
    RIDESHARE = "rideshare"
    LOCAL_RENTAL = "local_rental"
    EXPORT = "export"
    PARTS = "parts"


class DispositionOrder(Base):
    """FLT-007 — eligibility (evaluate_disposition_eligibility) is a
    plain age/mileage threshold check; channel routing is a human
    decision recorded here, not an algorithm's."""

    __tablename__ = "disposition_orders"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    channel: Mapped[DispositionChannel] = mapped_column(Enum(DispositionChannel), nullable=False)
    recovery_amount_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|sold
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class FloorplanFacility(Base):
    """FLT-009 — a credit line securing the fleet. utilized_amount_usd
    is tracked directly (pledging/releasing a vehicle updates it), not
    recomputed from collateral rows on every read. frozen is set
    autonomously by the Fleet Intelligence Agent the instant a covenant
    breach is detected — a real, immediate block on further pledges,
    not just a logged warning. Unfreezing is a human action."""

    __tablename__ = "floorplan_facilities"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lender: Mapped[str] = mapped_column(String(128), nullable=False)
    credit_limit_usd: Mapped[float] = mapped_column(Float, nullable=False)
    utilized_amount_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_utilization_pct: Mapped[float] = mapped_column(Float, nullable=False, default=90.0)
    frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    frozen_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class VinCollateralStatus(Base):
    __tablename__ = "vin_collateral_status"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    pledged_amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pledged")  # pledged|released
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DrivingEventType(str, enum.Enum):
    HARSH_BRAKING = "harsh_braking"
    RAPID_ACCELERATION = "rapid_acceleration"
    SPEEDING = "speeding"
    PHONE_USAGE_DETECTED = "phone_usage_detected"
    FORWARD_COLLISION_WARNING = "forward_collision_warning"


class DrivingEvent(Base):
    """FLT-010 — a fact ingested directly from telematics (the same
    domain as TelemetryReading), never AI-inferred. Recording that an
    event happened is deterministic; what it means in aggregate is the
    Fleet Intelligence Agent's job, evaluated separately."""

    __tablename__ = "driving_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    event_type: Mapped[DrivingEventType] = mapped_column(Enum(DrivingEventType), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class SafetyScoreAssessment(Base):
    """The Fleet Intelligence Agent's output — a deterministic, fully
    auditable weighted-event heuristic is the floor (the same
    discipline as the Member Experience Agent's churn scoring and
    HRM's attrition-risk scoring), never a black box. A signal for
    fleet ops and pricing to act on, never an automatic penalty
    against a member."""

    __tablename__ = "safety_score_assessments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class EnergyType(str, enum.Enum):
    GASOLINE = "gasoline"
    ELECTRIC = "electric"


class FuelLogEntry(Base):
    """FLT-011 — fuel/energy cost tracking. quantity is gallons for
    gasoline, kWh for electric — cost-per-mile is plain arithmetic over
    these facts, no AI involved. Motive-style fuel management, scoped
    to what actually applies to a subscription passenger fleet (no
    cargo-temperature or HOS/IFTA tracking — this isn't a commercial
    trucking operation)."""

    __tablename__ = "fuel_log_entries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    energy_type: Mapped[EnergyType] = mapped_column(Enum(EnergyType), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    odometer_miles: Mapped[int] = mapped_column(Integer, nullable=False)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ChargingSessionStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class ChargingSession(Base):
    """FLT-012 — EV charging management, the feature every commercial
    fleet platform researched (ChargePoint, Samsara, Geotab-integrated
    tools) now treats as table-stakes for a modern fleet. energy_added_kwh
    and cost_usd are facts recorded from the charging session, not
    estimated — the same discipline as FuelLogEntry."""

    __tablename__ = "charging_sessions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    station_location: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    energy_added_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[ChargingSessionStatus] = mapped_column(Enum(ChargingSessionStatus), nullable=False, default=ChargingSessionStatus.IN_PROGRESS)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BatteryHealthReading(Base):
    """State of charge (current usable range) and state of health
    (degradation vs. original capacity) — the two metrics every EV
    fleet platform researched treats as the foundation for charging
    decisions and battery-replacement planning. A fact ingested from
    the vehicle's telemetry, never AI-inferred; a degradation ALERT
    based on these facts is a deterministic threshold check, the same
    principle as FLT-004's maintenance alerts."""

    __tablename__ = "battery_health_readings"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    state_of_charge_pct: Mapped[float] = mapped_column(Float, nullable=False)
    state_of_health_pct: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class IntakeReadinessRecommendation(str, enum.Enum):
    PROVISION_READY = "provision_ready"
    PROVISION_WITH_FLAG = "provision_with_flag"
    HOLD_FOR_REVIEW = "hold_for_review"


class IntakeReadinessAssessment(Base):
    """The Fleet Intelligence Agent's output for FLT-001. This never
    touches IntakeInspection.passed — a human inspector's pass/fail
    determination is a safety fact, never AI-judged. This assessment
    is purely advisory: whether a passed inspection still warrants a
    caution flag before provisioning, based on free-text notes a fixed
    checklist can't capture. mark_provisioned in the API is the sole
    human-confirmed action that actually provisions a vehicle."""

    __tablename__ = "intake_readiness_assessments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    intake_inspection_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    recommendation: Mapped[IntakeReadinessRecommendation] = mapped_column(Enum(IntakeReadinessRecommendation), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class TelemetryAnomalyLevel(str, enum.Enum):
    BENIGN = "benign"
    NEEDS_INVESTIGATION = "needs_investigation"


class TelemetryAnomalyAssessment(Base):
    """The Fleet Intelligence Agent's output for FLT-002 geofence
    alerts only. TamperEvent is a categorically different, more severe
    security signal — it always publishes vehicle.tamper_detected for
    human security review and is NEVER routed through this AI
    assessment; the agent has no ability to dismiss or downgrade a
    tamper event, by design, not by convention."""

    __tablename__ = "telemetry_anomaly_assessments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    geofence_alert_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    assessment: Mapped[TelemetryAnomalyLevel] = mapped_column(Enum(TelemetryAnomalyLevel), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class MaintenanceTriageAssessment(Base):
    """The Fleet Intelligence Agent's output for FLT-004 — a
    prioritized read of currently open, unacknowledged alerts across
    the fleet. This never opens a work order or changes an alert's
    severity; DTC-triggered urgency stays exactly as
    evaluate_maintenance_need already sets it deterministically. This
    is purely a triage signal for a fleet ops standup."""

    __tablename__ = "maintenance_triage_assessments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    alert_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    priority_order: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # vehicle_id strings, highest priority first
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class IncidentSeverityLevel(str, enum.Enum):
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"


class IncidentSeverityAssessment(Base):
    """The Fleet Intelligence Agent's output for FLT-005 — triage
    only. This NEVER touches liability_assessment, assessed_by,
    status, or the total_loss fields — those remain exclusively
    human-entered, the sharpest deterministic carve-out on this
    platform, because liability has real legal and insurance
    consequences an AI must never be allowed to determine. This
    assessment exists only to help fleet ops prioritize which
    incidents need urgent attention first."""

    __tablename__ = "incident_severity_assessments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    severity: Mapped[IncidentSeverityLevel] = mapped_column(Enum(IncidentSeverityLevel), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ValuationAnomalyLevel(str, enum.Enum):
    NORMAL = "normal"
    FLAGGED = "flagged"


class ValuationAnomalyAssessment(Base):
    """The Fleet Intelligence Agent's output for FLT-006. This never
    touches the depreciation schedule or the market valuation record
    itself — straight-line depreciation math is a financial
    calculation, the same deterministic-only precedent as payroll math
    and TCO arithmetic elsewhere on this platform. This assessment
    exists only to flag a meaningful book-vs-market drift for finance
    review."""

    __tablename__ = "valuation_anomaly_assessments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    asset_valuation_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    deviation_pct: Mapped[float] = mapped_column(Float, nullable=False)
    assessment: Mapped[ValuationAnomalyLevel] = mapped_column(Enum(ValuationAnomalyLevel), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DispositionChannelRecommendationStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DispositionChannelRecommendation(Base):
    """The Fleet Intelligence Agent's output for FLT-007 — which
    cascade stage a disposition-eligible vehicle should move to next.
    create_disposition_order_autonomous (the agent's normal path)
    writes this as ACCEPTED and creates the real DispositionOrder in
    the same call. The PENDING/accept/reject path still exists for a
    paused-agent advisory-only fallback."""

    __tablename__ = "disposition_channel_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    recommended_channel: Mapped[DispositionChannel] = mapped_column(Enum(DispositionChannel), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[DispositionChannelRecommendationStatus] = mapped_column(Enum(DispositionChannelRecommendationStatus), nullable=False, default=DispositionChannelRecommendationStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class CovenantRiskLevel(str, enum.Enum):
    SAFE = "safe"
    APPROACHING = "approaching"
    BREACHED = "breached"


class CovenantRiskAssessment(Base):
    """The Fleet Intelligence Agent's output for FLT-008/009. This
    NEVER touches VinCollateralStatus or a facility's utilized_amount
    — those remain exclusively deterministic arithmetic, the sharpest
    carve-out on this platform alongside incident liability, because
    lenders require precise, auditable collateral certainty that must
    never be AI-judged. is_covenant_breached in floorplan_engine.py
    remains the sole source of truth for whether a breach actually
    occurred; this assessment adds an earlier "approaching" warning
    tier and a narrative summary for finance review, nothing more."""

    __tablename__ = "covenant_risk_assessments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    risk_level: Mapped[CovenantRiskLevel] = mapped_column(Enum(CovenantRiskLevel), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class FuelAnomalyLevel(str, enum.Enum):
    NORMAL = "normal"
    FLAGGED = "flagged"


class FuelAnomalyAssessment(Base):
    """The Fleet Intelligence Agent's output for FLT-011. This never
    touches cost_per_mile or efficiency arithmetic — both remain the
    plain, hand-verifiable calculations they already were, the same
    financial-calculation precedent as payroll and TCO elsewhere. This
    assessment exists only to flag entries suggesting fraud, a data
    entry error, or a maintenance-driven efficiency problem, for a
    fleet ops review — it never blocks or alters the logged entry."""

    __tablename__ = "fuel_anomaly_assessments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    fuel_log_entry_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    assessment: Mapped[FuelAnomalyLevel] = mapped_column(Enum(FuelAnomalyLevel), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class BatteryDegradationForecast(Base):
    """The Fleet Intelligence Agent's output for FLT-012. This never
    touches is_degradation_alert or the watchlist threshold check —
    both remain exactly the deterministic single-reading comparison
    they already were, since a battery nearing end-of-life is
    consequential enough that the ALERT trigger stays auditable
    arithmetic. This forecast is additive: a trend projection across
    multiple readings, giving fleet finance a proactive replacement-
    budget signal before the reactive threshold alert would fire."""

    __tablename__ = "battery_degradation_forecasts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    degradation_rate_pct_per_day: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    estimated_days_to_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class FleetOptimizationRecommendation(str, enum.Enum):
    RETAIN = "retain"
    REVIEW_FOR_DISPOSITION = "review_for_disposition"
    PRIORITIZE_DISPOSITION = "prioritize_disposition"


class FleetOptimizationAssessment(Base):
    """The Fleet Intelligence Agent's capstone output for FLT-013 —
    synthesizes TCO, availability, disposition eligibility, and
    valuation-anomaly signals from across FLT-006/007/013 into one
    retain-vs-dispose recommendation, the kind of cross-module
    judgment a fleet manager would otherwise make by checking several
    separate screens by hand. This never creates a disposition order,
    changes eligibility, or alters any of the underlying deterministic
    figures it reads — it is purely a prioritization signal on top of
    facts that already exist elsewhere."""

    __tablename__ = "fleet_optimization_assessments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    recommendation: Mapped[FleetOptimizationRecommendation] = mapped_column(Enum(FleetOptimizationRecommendation), nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
