"""
FLT-001 Fleet Inventory Master + FLT-002 Vehicle Acquisition & Intake.

The provisioning gate is a hard rule, not a warning: a vehicle cannot be
staged or assigned to a member until is_class_certified is true, and
certification itself requires the full provisioning chain (IoT device,
TLS cert with VIN as CN, card pairing) to be complete first. Software-
defined assets need their software defined before they go anywhere near
a member.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    IntakeInspection,
    IntakeReadinessAssessment,
    IntakeReadinessRecommendation,
    ProvisioningRecord,
    PurchaseOrder,
    PurchaseOrderStatus,
    Vehicle,
    VehicleClass,
    VehicleStatus,
)


class NotProvisionedError(ValueError):
    pass


async def create_purchase_order(
    db: AsyncSession, tenant_id: str, source: str, vehicle_description: str, cost_usd: float, created_by: str
) -> PurchaseOrder:
    po = PurchaseOrder(
        tenant_id=tenant_id, source=source, vehicle_description=vehicle_description,
        cost_usd=cost_usd, created_by=created_by,
    )
    db.add(po)
    await db.commit()
    await db.refresh(po)
    return po


async def receive_vehicle(
    db: AsyncSession,
    tenant_id: str,
    po_id: uuid.UUID,
    vin: str,
    make: str,
    model: str,
    year: int,
    vehicle_class: VehicleClass,
    ownership_entity: str,
) -> Vehicle | None:
    result = await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.tenant_id == tenant_id))
    po = result.scalar_one_or_none()
    if po is None or po.status != PurchaseOrderStatus.ORDERED:
        return None

    vehicle = Vehicle(
        tenant_id=tenant_id, vin=vin, make=make, model=model, year=year, vehicle_class=vehicle_class,
        status=VehicleStatus.IN_TRANSIT, ownership_entity=ownership_entity,
    )
    db.add(vehicle)
    await db.flush()
    po.status = PurchaseOrderStatus.RECEIVED
    po.vehicle_id = vehicle.id
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def record_inspection(
    db: AsyncSession, tenant_id: str, po_id: uuid.UUID, checklist: dict, passed: bool, inspected_by: str, notes: str = ""
) -> IntakeInspection | None:
    result = await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.tenant_id == tenant_id))
    po = result.scalar_one_or_none()
    if po is None or po.status != PurchaseOrderStatus.RECEIVED:
        return None

    inspection = IntakeInspection(
        tenant_id=tenant_id, purchase_order_id=po_id, checklist=checklist,
        passed=passed, inspected_by=inspected_by, notes=notes,
    )
    db.add(inspection)
    if passed:
        po.status = PurchaseOrderStatus.INSPECTED
    await db.commit()
    await db.refresh(inspection)
    return inspection


async def provision_vehicle(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, provisioned_by: str, card_id: str
) -> ProvisioningRecord:
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        raise ValueError(f"vehicle {vehicle_id} not found")

    record = ProvisioningRecord(
        tenant_id=tenant_id, vehicle_id=vehicle_id, iot_device_installed=True,
        tls_cert_common_name=vehicle.vin, tls_cert_issued_at=datetime.now(timezone.utc),
        card_paired=True, card_id=card_id, provisioned_by=provisioned_by,
    )
    db.add(record)
    vehicle.is_provisioned = True
    await db.commit()
    await db.refresh(record)
    return record


async def certify_class(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, certified_by: str) -> Vehicle:
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        raise ValueError(f"vehicle {vehicle_id} not found")
    if not vehicle.is_provisioned:
        raise NotProvisionedError(
            f"vehicle {vehicle.vin} cannot be class-certified before provisioning "
            "(IoT device, TLS cert, card pairing) is complete"
        )
    vehicle.is_class_certified = True
    vehicle.status = VehicleStatus.STAGED
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def get_vehicle(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> Vehicle | None:
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def search_vehicles(
    db: AsyncSession, tenant_id: str, vehicle_class: VehicleClass | None = None,
    status: VehicleStatus | None = None, hub_id: uuid.UUID | None = None,
) -> list[Vehicle]:
    stmt = select(Vehicle).where(Vehicle.tenant_id == tenant_id)
    if vehicle_class:
        stmt = stmt.where(Vehicle.vehicle_class == vehicle_class)
    if status:
        stmt = stmt.where(Vehicle.status == status)
    if hub_id:
        stmt = stmt.where(Vehicle.current_hub_id == hub_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_vehicle_status(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, status: VehicleStatus
) -> Vehicle | None:
    vehicle = await get_vehicle(db, tenant_id, vehicle_id)
    if vehicle is None:
        return None
    if status in (VehicleStatus.STAGED, VehicleStatus.ASSIGNED, VehicleStatus.RENTAL_POOL) and not vehicle.is_class_certified:
        raise NotProvisionedError(
            f"vehicle {vehicle.vin} is not class-certified — cannot be {status.value}"
        )
    vehicle.status = status
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def record_intake_readiness(
    db: AsyncSession, tenant_id: str, intake_inspection_id: uuid.UUID, recommendation: IntakeReadinessRecommendation, reasoning: str,
) -> IntakeReadinessAssessment:
    assessment = IntakeReadinessAssessment(tenant_id=tenant_id, intake_inspection_id=intake_inspection_id, recommendation=recommendation, reasoning=reasoning)
    db.add(assessment)
    await db.commit()
    await db.refresh(assessment)
    return assessment


async def auto_execute_intake_readiness(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, recommendation: IntakeReadinessRecommendation, reasoning: str, agent_key: str,
) -> Vehicle:
    """Fully autonomous FLT-001 execution: HOLD_FOR_REVIEW immediately
    blocks the vehicle (held_for_review=True — staging/assignment is
    already hard-blocked while unprovisioned, but this makes the hold
    an explicit, visible fact rather than an implicit side effect).
    PROVISION_READY and PROVISION_WITH_FLAG both auto-provision and
    auto-certify the vehicle in the same call — no human touches the
    keyboard for the common case; a flagged concern is a note for
    later, not a blocker."""
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        raise ValueError(f"vehicle {vehicle_id} not found")

    if recommendation == IntakeReadinessRecommendation.HOLD_FOR_REVIEW:
        vehicle.held_for_review = True
        vehicle.held_for_review_reason = reasoning
    else:
        vehicle.held_for_review = False
        vehicle.held_for_review_reason = ""
        record = ProvisioningRecord(
            tenant_id=tenant_id, vehicle_id=vehicle_id, iot_device_installed=True,
            tls_cert_common_name=vehicle.vin, tls_cert_issued_at=datetime.now(timezone.utc),
            card_paired=True, card_id=f"auto-{vehicle.vin}", provisioned_by=agent_key,
        )
        db.add(record)
        vehicle.is_provisioned = True
        vehicle.is_class_certified = True
        vehicle.status = VehicleStatus.STAGED

    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def get_intake_readiness(db: AsyncSession, tenant_id: str, intake_inspection_id: uuid.UUID) -> IntakeReadinessAssessment | None:
    result = await db.execute(
        select(IntakeReadinessAssessment).where(IntakeReadinessAssessment.tenant_id == tenant_id, IntakeReadinessAssessment.intake_inspection_id == intake_inspection_id)
        .order_by(IntakeReadinessAssessment.computed_at.desc())
    )
    return result.scalars().first()


async def get_intake_inspection(db: AsyncSession, tenant_id: str, intake_inspection_id: uuid.UUID) -> IntakeInspection | None:
    result = await db.execute(select(IntakeInspection).where(IntakeInspection.id == intake_inspection_id, IntakeInspection.tenant_id == tenant_id))
    return result.scalar_one_or_none()
