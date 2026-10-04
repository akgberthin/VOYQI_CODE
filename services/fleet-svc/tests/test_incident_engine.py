import uuid

from src.models import IncidentType, LiabilityAssessment, VehicleClass, VehicleStatus
from src.services import incident_engine, inventory_engine

TENANT = "voyqi-holdings"


async def _certified_vehicle(db_session, vin="1HGCM82633A004352"):
    po = await inventory_engine.create_purchase_order(
        db_session, tenant_id=TENANT, source="dealer", vehicle_description="test", cost_usd=50000.0, created_by="fleet-ops-1",
    )
    vehicle = await inventory_engine.receive_vehicle(
        db_session, tenant_id=TENANT, po_id=po.id, vin=vin, make="Test", model="Car", year=2024,
        vehicle_class=VehicleClass.EXECUTIVE, ownership_entity="x",
    )
    await inventory_engine.provision_vehicle(db_session, TENANT, vehicle.id, "fleet-ops-1", "VQ-1")
    await inventory_engine.certify_class(db_session, TENANT, vehicle.id, "fleet-ops-1")
    return vehicle


async def test_report_incident_pulls_vehicle_from_service(db_session):
    vehicle = await _certified_vehicle(db_session)
    incident = await incident_engine.report_incident(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, incident_type=IncidentType.ACCIDENT,
        description="Rear-ended at a stoplight", reported_by="member-1",
    )
    assert incident.status.value == "reported"
    assert incident.liability_assessment == LiabilityAssessment.UNDETERMINED

    updated = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert updated.status == VehicleStatus.MAINTENANCE


async def test_assess_liability(db_session):
    vehicle = await _certified_vehicle(db_session)
    incident = await incident_engine.report_incident(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, incident_type=IncidentType.DAMAGE,
        description="Curb damage", reported_by="member-1",
    )
    assessed = await incident_engine.assess_liability(db_session, TENANT, incident.id, LiabilityAssessment.MEMBER, "risk-team-1")
    assert assessed.liability_assessment == LiabilityAssessment.MEMBER
    assert assessed.status.value == "assessed"


async def test_insurance_handoff(db_session):
    vehicle = await _certified_vehicle(db_session)
    incident = await incident_engine.report_incident(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, incident_type=IncidentType.ACCIDENT,
        description="Collision", reported_by="member-1",
    )
    handed_off = await incident_engine.handoff_to_insurance(db_session, TENANT, incident.id, "Progressive", "CLM-99231")
    assert handed_off.status.value == "claim_filed"
    assert handed_off.claim_number == "CLM-99231"


async def test_repair_flow_restores_vehicle(db_session):
    vehicle = await _certified_vehicle(db_session)
    incident = await incident_engine.report_incident(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, incident_type=IncidentType.DAMAGE,
        description="Bumper damage", reported_by="member-1",
    )
    repair = await incident_engine.create_repair_order(db_session, TENANT, incident.id, "Downtown Body Shop", 3200.0)
    assert repair.status.value == "open"

    completed = await incident_engine.complete_repair(db_session, TENANT, repair.id)
    assert completed.status.value == "completed"

    updated_vehicle = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert updated_vehicle.status == VehicleStatus.STAGED


async def test_declare_total_loss(db_session):
    vehicle = await _certified_vehicle(db_session)
    incident = await incident_engine.report_incident(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, incident_type=IncidentType.ACCIDENT,
        description="Severe collision", reported_by="member-1",
    )
    total_loss = await incident_engine.declare_total_loss(
        db_session, TENANT, incident.id, "risk-team-1", "repair cost exceeds 80% of book value"
    )
    assert total_loss.status.value == "total_loss"

    updated_vehicle = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert updated_vehicle.status == VehicleStatus.DISPOSED


async def test_assess_liability_unknown_incident_returns_none(db_session):
    result = await incident_engine.assess_liability(db_session, TENANT, uuid.uuid4(), LiabilityAssessment.MEMBER, "x")
    assert result is None


async def test_record_and_get_incident_severity(db_session):
    from src.models import IncidentSeverityLevel

    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004390")
    incident = await incident_engine.report_incident(db_session, TENANT, vehicle.id, IncidentType.ACCIDENT, "minor fender bender in parking lot", "member-1")

    await incident_engine.record_incident_severity(db_session, TENANT, incident.id, IncidentSeverityLevel.MINOR, "Minor parking lot fender bender", "no injury or airbag keywords found")

    latest = await incident_engine.get_incident_severity(db_session, TENANT, incident.id)
    assert latest.severity.value == "minor"


async def test_get_incident_severity_none_when_not_assessed(db_session):
    result = await incident_engine.get_incident_severity(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_get_incident(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004391")
    incident = await incident_engine.report_incident(db_session, TENANT, vehicle.id, IncidentType.ACCIDENT, "test", "member-1")

    fetched = await incident_engine.get_incident(db_session, TENANT, incident.id)
    assert fetched.id == incident.id
    assert fetched.liability_assessment.value == "undetermined"  # confirms liability starts untouched by anything but assess_liability
