import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import (
    ChecklistKind,
    ChecklistStatus,
    CompensationReason,
    DocumentType,
    EmploymentStatus,
)
from src.services import employee_engine
from src.services.employee_engine import ChecklistIncompleteError

router = APIRouter(prefix="/api/v1/hrm", tags=["employee-directory"])


# ── Schemas ───────────────────────────────────────────────────────────

class EmployeeCreate(BaseModel):
    full_name: str
    job_title: str = ""
    department: str = ""
    entity_ref: str = ""
    email: str = ""
    phone: str = ""
    manager_id: uuid.UUID | None = None
    annual_salary_usd: float = 0.0


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str
    phone: str
    entity_ref: str
    job_title: str
    department: str
    manager_id: uuid.UUID | None
    employment_status: EmploymentStatus
    hire_date: datetime
    termination_date: datetime | None


class PositionCreate(BaseModel):
    title: str
    department: str = ""
    pay_grade: str = ""
    salary_band_min: float = 0.0
    salary_band_max: float = 0.0


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    department: str
    pay_grade: str
    salary_band_min: float
    salary_band_max: float
    is_vacant: bool


class ChecklistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    kind: ChecklistKind
    status: ChecklistStatus
    completed_at: datetime | None


class ChecklistTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    checklist_id: uuid.UUID
    label: str
    is_completed: bool


class TaskCreate(BaseModel):
    label: str


class DocumentCreate(BaseModel):
    document_type: DocumentType
    file_ref: str = ""


class SkillCreate(BaseModel):
    skill_name: str
    proficiency_level: str = ""


class CertificationCreate(BaseModel):
    name: str
    expires_at: datetime | None = None


class EmergencyContactCreate(BaseModel):
    name: str
    relationship: str = ""
    phone: str = ""


class CompensationCreate(BaseModel):
    annual_salary_usd: float
    reason: CompensationReason
    notes: str = ""


# ── Employees — AI-first: creation triggers onboarding-task recommendation ──

@router.post("/employees", response_model=EmployeeOut, status_code=201)
async def create_employee(
    body: EmployeeCreate, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    employee = await employee_engine.create_employee(
        db, tenant_id, body.full_name, body.job_title, body.department, body.entity_ref,
        body.email, body.phone, body.manager_id, body.annual_salary_usd,
    )
    checklist = await employee_engine.create_checklist(db, tenant_id, employee.id, ChecklistKind.ONBOARDING)
    # AI-first: the Talent Agent recommends role-appropriate onboarding
    # tasks the instant a new hire is created — not on request.
    background_tasks.add_task(
        publish_event,
        "hrm.employee",
        "hrm.employee_created",
        tenant_id,
        {"employee_id": str(employee.id), "checklist_id": str(checklist.id), "job_title": employee.job_title, "department": employee.department, "entity_ref": employee.entity_ref},
    )
    return employee


@router.get("/employees", response_model=list[EmployeeOut])
async def list_employees(department: str | None = None, status: EmploymentStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await employee_engine.list_employees(db, tenant_id, department=department, status=status)


@router.get("/employees/{employee_id}", response_model=EmployeeOut)
async def get_employee(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    employee = await employee_engine.get_employee(db, tenant_id, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@router.get("/employees/{manager_id}/direct-reports", response_model=list[EmployeeOut])
async def get_direct_reports(manager_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await employee_engine.get_direct_reports(db, tenant_id, manager_id)


class MatrixReportCreate(BaseModel):
    secondary_manager_id: uuid.UUID
    context: str = ""


@router.post("/employees/{employee_id}/matrix-report", status_code=201)
async def add_matrix_report(employee_id: uuid.UUID, body: MatrixReportCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    link = await employee_engine.add_matrix_report(db, tenant_id, employee_id, body.secondary_manager_id, body.context)
    return {"id": str(link.id), "employee_id": str(employee_id), "secondary_manager_id": str(body.secondary_manager_id)}


@router.post("/employees/{employee_id}/offboard", response_model=EmployeeOut)
async def initiate_offboarding(
    employee_id: uuid.UUID, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    employee = await employee_engine.initiate_offboarding(db, tenant_id, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    checklist = await employee_engine.create_checklist(db, tenant_id, employee_id, ChecklistKind.OFFBOARDING)
    # AI-first: offboarding tasks (access revocation, equipment return)
    # are recommended automatically too.
    background_tasks.add_task(
        publish_event,
        "hrm.employee",
        "hrm.offboarding_initiated",
        tenant_id,
        {"employee_id": str(employee_id), "checklist_id": str(checklist.id), "job_title": employee.job_title, "department": employee.department, "entity_ref": employee.entity_ref},
    )
    return employee


# ── Positions ─────────────────────────────────────────────────────────

@router.post("/positions", response_model=PositionOut, status_code=201)
async def create_position(body: PositionCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await employee_engine.create_position(db, tenant_id, body.title, body.department, body.pay_grade, body.salary_band_min, body.salary_band_max)


@router.get("/positions", response_model=list[PositionOut])
async def list_positions(vacant_only: bool = False, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await employee_engine.list_positions(db, tenant_id, vacant_only=vacant_only)


@router.post("/positions/{position_id}/fill", response_model=PositionOut)
async def fill_position(position_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    position = await employee_engine.fill_position(db, tenant_id, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


# ── Checklists ────────────────────────────────────────────────────────

@router.get("/employees/{employee_id}/checklists", response_model=list[ChecklistOut])
async def list_employee_checklists(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await employee_engine.list_checklists_for_employee(db, tenant_id, employee_id)


@router.post("/checklists/{checklist_id}/populate-now")
async def populate_checklist_now(checklist_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation employee
    creation/offboarding already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    checklist = await employee_engine.get_checklist(db, tenant_id, checklist_id)
    if checklist is None:
        raise HTTPException(status_code=404, detail="Checklist not found")
    employee = await employee_engine.get_employee(db, tenant_id, checklist.employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    response = ai_orchestration_tools.request_checklist_population(
        tenant_id, str(checklist_id), checklist.kind.value, employee.job_title, employee.department, employee.entity_ref,
    )
    if response is None:
        raise HTTPException(status_code=502, detail="Talent Agent unreachable")
    return response


@router.get("/checklists/{checklist_id}/tasks", response_model=list[ChecklistTaskOut])
async def list_checklist_tasks(checklist_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await employee_engine.list_checklist_tasks(db, tenant_id, checklist_id)


@router.post("/checklists/{checklist_id}/tasks", response_model=ChecklistTaskOut, status_code=201)
async def add_checklist_task(checklist_id: uuid.UUID, body: TaskCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await employee_engine.add_checklist_task(db, tenant_id, checklist_id, body.label)


@router.post("/checklist-tasks/{task_id}/check", response_model=ChecklistTaskOut)
async def check_task(task_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    task = await employee_engine.check_task(db, tenant_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/checklists/{checklist_id}/mark-complete", response_model=ChecklistOut)
async def mark_checklist_complete(checklist_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await employee_engine.mark_checklist_complete(db, tenant_id, checklist_id)
    except ChecklistIncompleteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── Document vault, skills, certifications, emergency contacts, comp ──

@router.post("/employees/{employee_id}/documents", status_code=201)
async def add_document(employee_id: uuid.UUID, body: DocumentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    doc = await employee_engine.add_document(db, tenant_id, employee_id, body.document_type, body.file_ref)
    return {"id": str(doc.id), "document_type": doc.document_type.value, "file_ref": doc.file_ref}


@router.get("/employees/{employee_id}/documents")
async def list_documents(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    docs = await employee_engine.list_documents(db, tenant_id, employee_id)
    return [{"id": str(d.id), "document_type": d.document_type.value, "file_ref": d.file_ref, "uploaded_at": d.uploaded_at.isoformat()} for d in docs]


@router.post("/employees/{employee_id}/skills", status_code=201)
async def add_skill(employee_id: uuid.UUID, body: SkillCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    skill = await employee_engine.add_skill(db, tenant_id, employee_id, body.skill_name, body.proficiency_level)
    return {"id": str(skill.id), "skill_name": skill.skill_name, "proficiency_level": skill.proficiency_level}


@router.get("/employees/{employee_id}/skills")
async def list_skills(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    skills = await employee_engine.list_skills(db, tenant_id, employee_id)
    return [{"id": str(s.id), "skill_name": s.skill_name, "proficiency_level": s.proficiency_level} for s in skills]


@router.post("/employees/{employee_id}/certifications", status_code=201)
async def add_certification(employee_id: uuid.UUID, body: CertificationCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    cert = await employee_engine.add_certification(db, tenant_id, employee_id, body.name, body.expires_at)
    return {"id": str(cert.id), "name": cert.name}


@router.get("/employees/{employee_id}/certifications")
async def list_certifications(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    certs = await employee_engine.list_certifications(db, tenant_id, employee_id)
    return [{"id": str(c.id), "name": c.name, "expires_at": c.expires_at.isoformat() if c.expires_at else None} for c in certs]


@router.post("/employees/{employee_id}/emergency-contacts", status_code=201)
async def add_emergency_contact(employee_id: uuid.UUID, body: EmergencyContactCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    contact = await employee_engine.add_emergency_contact(db, tenant_id, employee_id, body.name, body.relationship, body.phone)
    return {"id": str(contact.id), "name": contact.name}


@router.get("/employees/{employee_id}/emergency-contacts")
async def list_emergency_contacts(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    contacts = await employee_engine.list_emergency_contacts(db, tenant_id, employee_id)
    return [{"id": str(c.id), "name": c.name, "relationship": c.relationship, "phone": c.phone} for c in contacts]


@router.post("/employees/{employee_id}/compensation", status_code=201)
async def add_compensation_record(employee_id: uuid.UUID, body: CompensationCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    record = await employee_engine.add_compensation_record(db, tenant_id, employee_id, body.annual_salary_usd, body.reason, body.notes)
    return {"id": str(record.id), "annual_salary_usd": record.annual_salary_usd, "reason": record.reason.value}


@router.get("/employees/{employee_id}/compensation")
async def list_compensation_history(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    history = await employee_engine.list_compensation_history(db, tenant_id, employee_id)
    return [{"id": str(r.id), "annual_salary_usd": r.annual_salary_usd, "reason": r.reason.value, "effective_date": r.effective_date.isoformat()} for r in history]
