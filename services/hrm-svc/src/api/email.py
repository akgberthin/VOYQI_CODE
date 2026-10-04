import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import EmailRecipientScope, EmailStatus
from src.services import email_engine
from src.services.email_engine import EmailNotDraftError

router = APIRouter(prefix="/api/v1/hrm", tags=["email"])


class EmailDraftCreate(BaseModel):
    recipient_scope: EmailRecipientScope
    subject: str
    body: str
    recipient_employee_id: uuid.UUID | None = None


class EmailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipient_scope: EmailRecipientScope
    recipient_employee_id: uuid.UUID | None
    subject: str
    body: str
    ai_prompt: str
    is_ai_generated: bool
    status: EmailStatus
    created_at: datetime
    sent_at: datetime | None


@router.post("/emails", response_model=EmailOut, status_code=201)
async def create_email_draft(body: EmailDraftCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The manual-composition path — an administrator writes the
    email themselves. Still created in draft; send_email is the same
    required human step whether the body came from a person or the
    Talent Agent."""
    return await email_engine.create_email_draft(db, tenant_id, body.recipient_scope, body.subject, body.body, body.recipient_employee_id)


@router.get("/emails", response_model=list[EmailOut])
async def list_email_drafts(status: EmailStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await email_engine.list_email_drafts(db, tenant_id, status)


@router.get("/emails/{email_id}/recipient-count")
async def get_recipient_count(email_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    email = await email_engine.get_email(db, tenant_id, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    count = await email_engine.resolve_recipient_count(db, tenant_id, email.recipient_scope)
    return {"email_id": str(email_id), "recipient_scope": email.recipient_scope.value, "recipient_count": count}


@router.post("/emails/{email_id}/send")
async def send_email(email_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        email = await email_engine.send_email(db, tenant_id, email_id)
    except EmailNotDraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    count = await email_engine.resolve_recipient_count(db, tenant_id, email.recipient_scope)
    return {"id": str(email.id), "status": email.status.value, "recipient_count": count}


@router.post("/emails/request-ai-draft", status_code=202)
async def request_ai_email_draft(
    recipient_scope: EmailRecipientScope, prompt: str, background_tasks: BackgroundTasks,
    recipient_employee_id: uuid.UUID | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    background_tasks.add_task(
        publish_event,
        "hrm.email",
        "hrm.email_draft_requested",
        tenant_id,
        {"recipient_scope": recipient_scope.value, "recipient_employee_id": str(recipient_employee_id) if recipient_employee_id else None, "prompt": prompt},
    )
    return {"status": "email_draft_requested"}


@router.post("/emails/draft-ai-now")
async def draft_ai_email_now(
    recipient_scope: EmailRecipientScope, prompt: str, recipient_employee_id: uuid.UUID | None = None,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Synchronous variant of the same AI-first evaluation the
    request endpoint already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    response = ai_orchestration_tools.request_email_draft(tenant_id, recipient_scope.value, prompt, str(recipient_employee_id) if recipient_employee_id else None)
    if response is None:
        raise HTTPException(status_code=502, detail="Talent Agent unreachable")
    return response
