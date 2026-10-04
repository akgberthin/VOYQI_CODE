"""
HRM-001 employee directory engine.

create_employee publishes hrm.employee_created unconditionally — this
is what makes onboarding-checklist task recommendation AI-first rather
than a manual step someone has to remember: the Talent Agent evaluates
every new hire immediately and populates role-appropriate tasks.
initiate_offboarding does the same for offboarding. mark_checklist_complete
is the one hard gate in this engine — every task must be done first, no
exceptions, no judgment call to make.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    CertificationRecord,
    ChecklistKind,
    ChecklistStatus,
    ChecklistTask,
    CompensationReason,
    CompensationRecord,
    DocumentType,
    DocumentVaultEntry,
    EmergencyContact,
    EmployeeChecklist,
    EmployeeProfile,
    EmploymentStatus,
    MatrixReportingLink,
    Position,
    SkillRecord,
)


class ChecklistIncompleteError(ValueError):
    pass


# ── Employee profiles ──────────────────────────────────────────────────

async def create_employee(
    db: AsyncSession, tenant_id: str, full_name: str, job_title: str = "", department: str = "",
    entity_ref: str = "", email: str = "", phone: str = "", manager_id: uuid.UUID | None = None,
    annual_salary_usd: float = 0.0,
) -> EmployeeProfile:
    employee = EmployeeProfile(
        tenant_id=tenant_id, full_name=full_name, job_title=job_title, department=department,
        entity_ref=entity_ref, email=email, phone=phone, manager_id=manager_id,
    )
    db.add(employee)
    await db.flush()
    if annual_salary_usd:
        db.add(CompensationRecord(tenant_id=tenant_id, employee_id=employee.id, annual_salary_usd=annual_salary_usd, reason=CompensationReason.HIRE))
    await db.commit()
    await db.refresh(employee)
    return employee


async def get_employee(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> EmployeeProfile | None:
    result = await db.execute(select(EmployeeProfile).where(EmployeeProfile.id == employee_id, EmployeeProfile.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_employees(db: AsyncSession, tenant_id: str, department: str | None = None, status: EmploymentStatus | None = None) -> list[EmployeeProfile]:
    stmt = select(EmployeeProfile).where(EmployeeProfile.tenant_id == tenant_id)
    if department:
        stmt = stmt.where(EmployeeProfile.department == department)
    if status:
        stmt = stmt.where(EmployeeProfile.employment_status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_direct_reports(db: AsyncSession, tenant_id: str, manager_id: uuid.UUID) -> list[EmployeeProfile]:
    result = await db.execute(select(EmployeeProfile).where(EmployeeProfile.tenant_id == tenant_id, EmployeeProfile.manager_id == manager_id))
    return list(result.scalars().all())


async def add_matrix_report(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, secondary_manager_id: uuid.UUID, context: str = "") -> MatrixReportingLink:
    link = MatrixReportingLink(tenant_id=tenant_id, employee_id=employee_id, secondary_manager_id=secondary_manager_id, context=context)
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


async def initiate_offboarding(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, termination_date: datetime | None = None) -> EmployeeProfile | None:
    employee = await get_employee(db, tenant_id, employee_id)
    if employee is None:
        return None
    employee.employment_status = EmploymentStatus.TERMINATED
    employee.termination_date = termination_date or datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(employee)
    return employee


# ── Positions (job catalog) ────────────────────────────────────────────

async def create_position(db: AsyncSession, tenant_id: str, title: str, department: str = "", pay_grade: str = "", salary_band_min: float = 0.0, salary_band_max: float = 0.0) -> Position:
    position = Position(tenant_id=tenant_id, title=title, department=department, pay_grade=pay_grade, salary_band_min=salary_band_min, salary_band_max=salary_band_max)
    db.add(position)
    await db.commit()
    await db.refresh(position)
    return position


async def list_positions(db: AsyncSession, tenant_id: str, vacant_only: bool = False) -> list[Position]:
    stmt = select(Position).where(Position.tenant_id == tenant_id)
    if vacant_only:
        stmt = stmt.where(Position.is_vacant)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def fill_position(db: AsyncSession, tenant_id: str, position_id: uuid.UUID) -> Position | None:
    result = await db.execute(select(Position).where(Position.id == position_id, Position.tenant_id == tenant_id))
    position = result.scalar_one_or_none()
    if position is None:
        return None
    position.is_vacant = False
    await db.commit()
    await db.refresh(position)
    return position


# ── Onboarding / offboarding checklists (Talent Agent populates tasks) ──

async def create_checklist(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, kind: ChecklistKind) -> EmployeeChecklist:
    checklist = EmployeeChecklist(tenant_id=tenant_id, employee_id=employee_id, kind=kind)
    db.add(checklist)
    await db.commit()
    await db.refresh(checklist)
    return checklist


async def get_checklist(db: AsyncSession, tenant_id: str, checklist_id: uuid.UUID) -> EmployeeChecklist | None:
    result = await db.execute(select(EmployeeChecklist).where(EmployeeChecklist.id == checklist_id, EmployeeChecklist.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_checklists_for_employee(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[EmployeeChecklist]:
    result = await db.execute(select(EmployeeChecklist).where(EmployeeChecklist.tenant_id == tenant_id, EmployeeChecklist.employee_id == employee_id))
    return list(result.scalars().all())


async def add_checklist_task(db: AsyncSession, tenant_id: str, checklist_id: uuid.UUID, label: str) -> ChecklistTask:
    task = ChecklistTask(tenant_id=tenant_id, checklist_id=checklist_id, label=label)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def list_checklist_tasks(db: AsyncSession, tenant_id: str, checklist_id: uuid.UUID) -> list[ChecklistTask]:
    result = await db.execute(select(ChecklistTask).where(ChecklistTask.tenant_id == tenant_id, ChecklistTask.checklist_id == checklist_id))
    return list(result.scalars().all())


async def check_task(db: AsyncSession, tenant_id: str, task_id: uuid.UUID) -> ChecklistTask | None:
    result = await db.execute(select(ChecklistTask).where(ChecklistTask.id == task_id, ChecklistTask.tenant_id == tenant_id))
    task = result.scalar_one_or_none()
    if task is None:
        return None
    task.is_completed = True
    await db.commit()
    await db.refresh(task)
    return task


async def mark_checklist_complete(db: AsyncSession, tenant_id: str, checklist_id: uuid.UUID) -> EmployeeChecklist:
    checklist = await get_checklist(db, tenant_id, checklist_id)
    if checklist is None:
        raise ValueError(f"checklist {checklist_id} not found")

    tasks = await list_checklist_tasks(db, tenant_id, checklist_id)
    incomplete = [t for t in tasks if not t.is_completed]
    if incomplete:
        raise ChecklistIncompleteError(f"{len(incomplete)} task(s) still open — every task must be checked first")

    checklist.status = ChecklistStatus.COMPLETED
    checklist.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(checklist)
    return checklist


# ── Document vault, skills, certifications, emergency contacts, comp ──

async def add_document(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, document_type: DocumentType, file_ref: str = "") -> DocumentVaultEntry:
    doc = DocumentVaultEntry(tenant_id=tenant_id, employee_id=employee_id, document_type=document_type, file_ref=file_ref)
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def list_documents(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[DocumentVaultEntry]:
    result = await db.execute(select(DocumentVaultEntry).where(DocumentVaultEntry.tenant_id == tenant_id, DocumentVaultEntry.employee_id == employee_id))
    return list(result.scalars().all())


async def add_skill(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, skill_name: str, proficiency_level: str = "") -> SkillRecord:
    skill = SkillRecord(tenant_id=tenant_id, employee_id=employee_id, skill_name=skill_name, proficiency_level=proficiency_level)
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


async def list_skills(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[SkillRecord]:
    result = await db.execute(select(SkillRecord).where(SkillRecord.tenant_id == tenant_id, SkillRecord.employee_id == employee_id))
    return list(result.scalars().all())


async def add_certification(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, name: str, expires_at: datetime | None = None) -> CertificationRecord:
    cert = CertificationRecord(tenant_id=tenant_id, employee_id=employee_id, name=name, expires_at=expires_at)
    db.add(cert)
    await db.commit()
    await db.refresh(cert)
    return cert


async def list_certifications(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[CertificationRecord]:
    result = await db.execute(select(CertificationRecord).where(CertificationRecord.tenant_id == tenant_id, CertificationRecord.employee_id == employee_id))
    return list(result.scalars().all())


async def add_emergency_contact(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, name: str, relationship: str = "", phone: str = "") -> EmergencyContact:
    contact = EmergencyContact(tenant_id=tenant_id, employee_id=employee_id, name=name, relationship=relationship, phone=phone)
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact


async def list_emergency_contacts(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[EmergencyContact]:
    result = await db.execute(select(EmergencyContact).where(EmergencyContact.tenant_id == tenant_id, EmergencyContact.employee_id == employee_id))
    return list(result.scalars().all())


async def add_compensation_record(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, annual_salary_usd: float, reason: CompensationReason, notes: str = "") -> CompensationRecord:
    record = CompensationRecord(tenant_id=tenant_id, employee_id=employee_id, annual_salary_usd=annual_salary_usd, reason=reason, notes=notes)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def list_compensation_history(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[CompensationRecord]:
    result = await db.execute(
        select(CompensationRecord).where(CompensationRecord.tenant_id == tenant_id, CompensationRecord.employee_id == employee_id).order_by(CompensationRecord.effective_date.desc())
    )
    return list(result.scalars().all())
