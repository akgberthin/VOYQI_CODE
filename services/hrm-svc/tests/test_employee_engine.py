import uuid

import pytest

from src.models import ChecklistKind, CompensationReason, DocumentType, EmploymentStatus
from src.services import employee_engine
from src.services.employee_engine import ChecklistIncompleteError

TENANT = "voyqi-holdings"


async def test_create_employee_starts_active(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe", job_title="Service Advisor", department="Dealership")
    assert emp.employment_status == EmploymentStatus.ACTIVE


async def test_create_employee_with_salary_records_compensation(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe", annual_salary_usd=65000.0)
    history = await employee_engine.list_compensation_history(db_session, TENANT, emp.id)
    assert len(history) == 1
    assert history[0].reason == CompensationReason.HIRE
    assert history[0].annual_salary_usd == 65000.0


async def test_direct_reports(db_session):
    manager = await employee_engine.create_employee(db_session, TENANT, "Manager Person")
    report1 = await employee_engine.create_employee(db_session, TENANT, "Report One", manager_id=manager.id)
    await employee_engine.create_employee(db_session, TENANT, "Unrelated Person")

    reports = await employee_engine.get_direct_reports(db_session, TENANT, manager.id)
    assert len(reports) == 1
    assert reports[0].id == report1.id


async def test_matrix_reporting_link(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    secondary = await employee_engine.create_employee(db_session, TENANT, "Dotted Line Manager")
    link = await employee_engine.add_matrix_report(db_session, TENANT, emp.id, secondary.id, context="Regional oversight")
    assert link.secondary_manager_id == secondary.id


async def test_initiate_offboarding(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    offboarded = await employee_engine.initiate_offboarding(db_session, TENANT, emp.id)
    assert offboarded.employment_status == EmploymentStatus.TERMINATED
    assert offboarded.termination_date is not None


async def test_position_lifecycle(db_session):
    position = await employee_engine.create_position(db_session, TENANT, "Service Advisor", department="Dealership", pay_grade="G4", salary_band_min=45000, salary_band_max=65000)
    assert position.is_vacant is True

    filled = await employee_engine.fill_position(db_session, TENANT, position.id)
    assert filled.is_vacant is False

    vacant = await employee_engine.list_positions(db_session, TENANT, vacant_only=True)
    assert position.id not in [p.id for p in vacant]


async def test_checklist_hard_gate_blocks_incomplete_tasks(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    checklist = await employee_engine.create_checklist(db_session, TENANT, emp.id, ChecklistKind.ONBOARDING)
    await employee_engine.add_checklist_task(db_session, TENANT, checklist.id, "Provision IT account")
    await employee_engine.add_checklist_task(db_session, TENANT, checklist.id, "Assign equipment")

    with pytest.raises(ChecklistIncompleteError):
        await employee_engine.mark_checklist_complete(db_session, TENANT, checklist.id)


async def test_checklist_completes_when_all_tasks_checked(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    checklist = await employee_engine.create_checklist(db_session, TENANT, emp.id, ChecklistKind.ONBOARDING)
    task1 = await employee_engine.add_checklist_task(db_session, TENANT, checklist.id, "Provision IT account")
    task2 = await employee_engine.add_checklist_task(db_session, TENANT, checklist.id, "Assign equipment")

    await employee_engine.check_task(db_session, TENANT, task1.id)
    await employee_engine.check_task(db_session, TENANT, task2.id)

    completed = await employee_engine.mark_checklist_complete(db_session, TENANT, checklist.id)
    assert completed.status.value == "completed"
    assert completed.completed_at is not None


async def test_offboarding_checklist_same_hard_gate(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    checklist = await employee_engine.create_checklist(db_session, TENANT, emp.id, ChecklistKind.OFFBOARDING)
    await employee_engine.add_checklist_task(db_session, TENANT, checklist.id, "Revoke IT access")

    with pytest.raises(ChecklistIncompleteError):
        await employee_engine.mark_checklist_complete(db_session, TENANT, checklist.id)


async def test_mark_complete_unknown_checklist_raises(db_session):
    with pytest.raises(ValueError):
        await employee_engine.mark_checklist_complete(db_session, TENANT, uuid.uuid4())


async def test_document_vault(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    await employee_engine.add_document(db_session, TENANT, emp.id, DocumentType.CONTRACT, file_ref="s3://docs/contract.pdf")
    await employee_engine.add_document(db_session, TENANT, emp.id, DocumentType.NDA)

    docs = await employee_engine.list_documents(db_session, TENANT, emp.id)
    assert len(docs) == 2


async def test_skills_and_certifications(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    await employee_engine.add_skill(db_session, TENANT, emp.id, "Automotive diagnostics", proficiency_level="expert")
    await employee_engine.add_certification(db_session, TENANT, emp.id, "ASE Master Technician")

    skills = await employee_engine.list_skills(db_session, TENANT, emp.id)
    certs = await employee_engine.list_certifications(db_session, TENANT, emp.id)
    assert len(skills) == 1
    assert len(certs) == 1


async def test_emergency_contacts(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    await employee_engine.add_emergency_contact(db_session, TENANT, emp.id, "John Doe", relationship="spouse", phone="305-555-0199")
    contacts = await employee_engine.list_emergency_contacts(db_session, TENANT, emp.id)
    assert len(contacts) == 1


async def test_compensation_history_ordered_most_recent_first(db_session):
    from datetime import datetime, timedelta, timezone

    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe", annual_salary_usd=60000.0)
    first = await employee_engine.list_compensation_history(db_session, TENANT, emp.id)
    row = await db_session.get(type(first[0]), first[0].id)
    row.effective_date = datetime.now(timezone.utc) - timedelta(days=365)
    await db_session.commit()

    await employee_engine.add_compensation_record(db_session, TENANT, emp.id, 68000.0, CompensationReason.MERIT, notes="Annual merit increase")

    history = await employee_engine.list_compensation_history(db_session, TENANT, emp.id)
    assert len(history) == 2
    assert history[0].reason == CompensationReason.MERIT


async def test_list_employees_filters_by_department_and_status(db_session):
    await employee_engine.create_employee(db_session, TENANT, "A", department="Sales")
    emp_b = await employee_engine.create_employee(db_session, TENANT, "B", department="Service")
    await employee_engine.initiate_offboarding(db_session, TENANT, emp_b.id)

    sales = await employee_engine.list_employees(db_session, TENANT, department="Sales")
    assert len(sales) == 1

    terminated = await employee_engine.list_employees(db_session, TENANT, status=EmploymentStatus.TERMINATED)
    assert len(terminated) == 1


async def test_list_checklists_for_employee(db_session):
    emp = await employee_engine.create_employee(db_session, TENANT, "Jane Doe")
    await employee_engine.create_checklist(db_session, TENANT, emp.id, ChecklistKind.ONBOARDING)
    await employee_engine.create_checklist(db_session, TENANT, emp.id, ChecklistKind.OFFBOARDING)

    checklists = await employee_engine.list_checklists_for_employee(db_session, TENANT, emp.id)
    assert len(checklists) == 2
