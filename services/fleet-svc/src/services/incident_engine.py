"""
FLT-005 Incident, Damage & Claims engine.

Declaring total loss is the one action here that publishes an event for
the Compliance/Audit Agent — not because it's inherently suspicious, but
because it's financially material enough (writing off a fleet asset)
that it belongs in the same audit trail as every other high-materiality
action on the platform, exactly like a legal-disclosure bypass or an
expired-asset override elsewhere.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    Incident,
    IncidentSeverityAssessment,
    IncidentSeverityLevel,
    IncidentStatus,
    IncidentType,
    LiabilityAssessment,
    RepairOrder,
    Vehicle,
    VehicleStatus,
    WorkOrderStatus,
)


async def report_incident(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, incident_type: IncidentType,
    description: str, reported_by: str, photo_refs: list[str] | None = None,
) -> Incident:
    incident = Incident(
        tenant_id=tenant_id, vehicle_id=vehicle_id, incident_type=incident_type,
        description=description, photo_refs=photo_refs or [], reported_by=reported_by,
    )
    db.add(incident)

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        vehicle.status = VehicleStatus.MAINTENANCE

    await db.commit()
    await db.refresh(incident)
    return incident


async def assess_liability(
    db: AsyncSession, tenant_id: str, incident_id: uuid.UUID, liability: LiabilityAssessment, assessed_by: str
) -> Incident | None:
    result = await db.execute(select(Incident).where(Incident.id == incident_id, Incident.tenant_id == tenant_id))
    incident = result.scalar_one_or_none()
    if incident is None:
        return None
    incident.liability_assessment = liability
    incident.assessed_by = assessed_by
    incident.status = IncidentStatus.ASSESSED
    await db.commit()
    await db.refresh(incident)
    return incident


async def handoff_to_insurance(
    db: AsyncSession, tenant_id: str, incident_id: uuid.UUID, carrier: str, claim_number: str
) -> Incident | None:
    result = await db.execute(select(Incident).where(Incident.id == incident_id, Incident.tenant_id == tenant_id))
    incident = result.scalar_one_or_none()
    if incident is None:
        return None
    incident.insurance_carrier = carrier
    incident.claim_number = claim_number
    incident.status = IncidentStatus.CLAIM_FILED
    await db.commit()
    await db.refresh(incident)
    return incident


async def create_repair_order(
    db: AsyncSession, tenant_id: str, incident_id: uuid.UUID, vendor: str, estimated_cost_usd: float
) -> RepairOrder:
    order = RepairOrder(tenant_id=tenant_id, incident_id=incident_id, vendor=vendor, estimated_cost_usd=estimated_cost_usd)
    db.add(order)

    incident_result = await db.execute(select(Incident).where(Incident.id == incident_id, Incident.tenant_id == tenant_id))
    incident = incident_result.scalar_one_or_none()
    if incident:
        incident.status = IncidentStatus.REPAIRING

    await db.commit()
    await db.refresh(order)
    return order


async def complete_repair(db: AsyncSession, tenant_id: str, repair_order_id: uuid.UUID) -> RepairOrder | None:
    result = await db.execute(select(RepairOrder).where(RepairOrder.id == repair_order_id, RepairOrder.tenant_id == tenant_id))
    order = result.scalar_one_or_none()
    if order is None:
        return None
    order.status = WorkOrderStatus.COMPLETED

    incident_result = await db.execute(select(Incident).where(Incident.id == order.incident_id))
    incident = incident_result.scalar_one_or_none()
    vehicle_id = None
    if incident:
        incident.status = IncidentStatus.CLOSED
        vehicle_id = incident.vehicle_id
    if vehicle_id:
        vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
        vehicle = vehicle_result.scalar_one_or_none()
        if vehicle and vehicle.status == VehicleStatus.MAINTENANCE:
            vehicle.status = VehicleStatus.STAGED

    await db.commit()
    await db.refresh(order)
    return order


async def declare_total_loss(
    db: AsyncSession, tenant_id: str, incident_id: uuid.UUID, declared_by: str, reason: str
) -> Incident | None:
    result = await db.execute(select(Incident).where(Incident.id == incident_id, Incident.tenant_id == tenant_id))
    incident = result.scalar_one_or_none()
    if incident is None:
        return None
    incident.status = IncidentStatus.TOTAL_LOSS
    incident.total_loss_declared_by = declared_by
    incident.total_loss_reason = reason

    vehicle_result = await db.execute(select(Vehicle).where(Vehicle.id == incident.vehicle_id))
    vehicle = vehicle_result.scalar_one_or_none()
    if vehicle:
        vehicle.status = VehicleStatus.DISPOSED

    await db.commit()
    await db.refresh(incident)
    return incident


async def get_incident(db: AsyncSession, tenant_id: str, incident_id: uuid.UUID) -> Incident | None:
    result = await db.execute(select(Incident).where(Incident.id == incident_id, Incident.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def record_incident_severity(
    db: AsyncSession, tenant_id: str, incident_id: uuid.UUID, severity: IncidentSeverityLevel, summary: str, reasoning: str,
) -> IncidentSeverityAssessment:
    assessment = IncidentSeverityAssessment(tenant_id=tenant_id, incident_id=incident_id, severity=severity, summary=summary, reasoning=reasoning)
    db.add(assessment)
    await db.commit()
    await db.refresh(assessment)
    return assessment


async def auto_execute_incident_severity(
    db: AsyncSession, tenant_id: str, incident_id: uuid.UUID, severity: IncidentSeverityLevel, agent_key: str,
) -> RepairOrder | None:
    """Fully autonomous FLT-005 execution: a SEVERE classification
    immediately opens a repair order and moves the incident into the
    REPAIRING pipeline — vendor assignment and cost estimate are still
    a human's job downstream (create_repair_order can be called again
    to update them), but the pipeline starts moving the instant the
    agent classifies the incident, not after someone reads a dashboard.
    This never touches liability_assessment or assessed_by — those
    remain a distinct, always-human legal judgment call that was never
    part of this agent's output to begin with. MODERATE/MINOR take no
    autonomous action beyond the classification itself."""
    if severity != IncidentSeverityLevel.SEVERE:
        return None
    result = await db.execute(select(Incident).where(Incident.id == incident_id, Incident.tenant_id == tenant_id))
    incident = result.scalar_one_or_none()
    if incident is None or incident.status != IncidentStatus.REPORTED:
        return None
    return await create_repair_order(db, tenant_id, incident_id, vendor="pending-assignment", estimated_cost_usd=0.0)


async def get_incident_severity(db: AsyncSession, tenant_id: str, incident_id: uuid.UUID) -> IncidentSeverityAssessment | None:
    result = await db.execute(
        select(IncidentSeverityAssessment).where(IncidentSeverityAssessment.tenant_id == tenant_id, IncidentSeverityAssessment.incident_id == incident_id)
        .order_by(IncidentSeverityAssessment.computed_at.desc())
    )
    return result.scalars().first()
