import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import CaseDraftReplyStatus, CaseMessageDirection, CaseStatus, CaseType
from src.services import case_management_engine
from src.services.case_management_engine import DraftReplyNotPendingError

router = APIRouter(prefix="/api/v1/hrm", tags=["case-management"])


class CaseCreate(BaseModel):
    employee_id: uuid.UUID
    case_type: CaseType
    subject: str


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    case_type: CaseType
    subject: str
    status: CaseStatus
    sla_due_at: datetime


class MessageCreate(BaseModel):
    direction: CaseMessageDirection
    body: str
    sender: str = ""


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    direction: CaseMessageDirection
    body: str
    sender: str
    created_at: datetime


class PolicyDocumentCreate(BaseModel):
    title: str
    body: str
    category: str = ""


class PolicyDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    body: str
    category: str


class DraftReplyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_id: uuid.UUID
    draft_body: str
    reasoning: str
    cites_policy_id: uuid.UUID | None
    status: CaseDraftReplyStatus
    reviewed_at: datetime | None


class ApproveRequest(BaseModel):
    final_body: str | None = None


@router.post("/cases", response_model=CaseOut, status_code=201)
async def create_case(body: CaseCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await case_management_engine.create_case(db, tenant_id, body.employee_id, body.case_type, body.subject)


@router.get("/cases", response_model=list[CaseOut])
async def list_cases(status: CaseStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await case_management_engine.list_cases(db, tenant_id, status)


@router.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(case_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    case = await case_management_engine.get_case(db, tenant_id, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.post("/cases/{case_id}/status")
async def update_case_status(
    case_id: uuid.UUID, status: CaseStatus, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    case = await case_management_engine.update_case_status(db, tenant_id, case_id, status)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    # AI-first: resolving a case automatically logs a note on the
    # employee's record — documentation, not a decision, so this
    # doesn't need human approval before it's written.
    if status == CaseStatus.RESOLVED:
        background_tasks.add_task(
            publish_event,
            "hrm.case_management",
            "hrm.case_resolved",
            tenant_id,
            {"employee_id": str(case.employee_id), "case_type": case.case_type.value, "subject": case.subject},
        )
    return {"id": str(case.id), "status": case.status.value}


@router.post("/cases/{case_id}/messages", response_model=MessageOut, status_code=201)
async def add_message(
    case_id: uuid.UUID, body: MessageCreate, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    case = await case_management_engine.get_case(db, tenant_id, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    message = await case_management_engine.add_message(db, tenant_id, case_id, body.direction, body.body, body.sender)
    # AI-first, but deliberately scoped: only inbound policy questions
    # ever get an AI-drafted reply. Complaints are excluded entirely —
    # create_case already escalated them and they never reach this path.
    if body.direction == CaseMessageDirection.INBOUND and case.case_type == CaseType.POLICY_QUESTION:
        background_tasks.add_task(
            publish_event,
            "hrm.case_management",
            "hrm.policy_question_asked",
            tenant_id,
            {"case_id": str(case_id), "question": body.body},
        )
    return message


@router.get("/cases/{case_id}/messages", response_model=list[MessageOut])
async def list_messages(case_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await case_management_engine.list_messages(db, tenant_id, case_id)


@router.post("/policy-documents", response_model=PolicyDocumentOut, status_code=201)
async def create_policy_document(body: PolicyDocumentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await case_management_engine.create_policy_document(db, tenant_id, body.title, body.body, body.category)


@router.get("/policy-documents", response_model=list[PolicyDocumentOut])
async def list_policy_documents(category: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await case_management_engine.list_policy_documents(db, tenant_id, category)


@router.post("/cases/{case_id}/answer-policy-question-now")
async def answer_policy_question_now(case_id: uuid.UUID, question: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation
    add_message already triggers asynchronously for policy questions."""
    from src.services import ai_orchestration_tools

    case = await case_management_engine.get_case(db, tenant_id, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    response = ai_orchestration_tools.request_policy_answer(tenant_id, str(case_id), question)
    if response is None:
        raise HTTPException(status_code=502, detail="Talent Agent unreachable")
    return response


class CaseDraftCreate(BaseModel):
    draft_body: str
    reasoning: str = ""
    cites_policy_id: uuid.UUID | None = None


@router.post("/cases/{case_id}/draft-replies", response_model=DraftReplyOut, status_code=201)
async def create_case_draft_reply(case_id: uuid.UUID, body: CaseDraftCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await case_management_engine.create_case_draft_reply(db, tenant_id, case_id, body.draft_body, body.reasoning, body.cites_policy_id)


@router.get("/cases/{case_id}/draft-replies/pending", response_model=list[DraftReplyOut])
async def list_pending_case_drafts(case_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await case_management_engine.list_pending_case_drafts(db, tenant_id, case_id)


@router.post("/draft-replies/{draft_id}/approve-and-send")
async def approve_and_send_reply(draft_id: uuid.UUID, body: ApproveRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        draft, message = await case_management_engine.approve_and_send_reply(db, tenant_id, draft_id, body.final_body)
    except DraftReplyNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"draft_id": str(draft.id), "message_id": str(message.id), "body": message.body}


@router.post("/draft-replies/{draft_id}/reject", response_model=DraftReplyOut)
async def reject_case_draft(draft_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    draft = await case_management_engine.reject_case_draft(db, tenant_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=409, detail="Draft not found or already reviewed")
    return draft
