"""
HRM-008 (extension) — Formal Email Communication.

Every email, whether written by an administrator or drafted by the
Talent Agent from a short prompt, is created in DRAFT status. There
is no path — AI or manual — that skips send_email, the one human-
confirmed action that actually dispatches it. This platform has no
integrated email/SMTP gateway (that would need a real SendGrid-style
integration outside this codebase's scope), so "sent" here means the
administrator confirmed dispatch — the same honesty already applied
to Notice's email/sms channels.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    EmailCommunication,
    EmailRecipientScope,
    EmailStatus,
    EmployeeProfile,
    EmploymentStatus,
)


class EmailNotDraftError(ValueError):
    pass


async def create_email_draft(
    db: AsyncSession, tenant_id: str, recipient_scope: EmailRecipientScope, subject: str, body: str,
    recipient_employee_id: uuid.UUID | None = None, ai_prompt: str = "", is_ai_generated: bool = False,
) -> EmailCommunication:
    email = EmailCommunication(
        tenant_id=tenant_id, recipient_scope=recipient_scope, recipient_employee_id=recipient_employee_id,
        subject=subject, body=body, ai_prompt=ai_prompt, is_ai_generated=is_ai_generated,
    )
    db.add(email)
    await db.commit()
    await db.refresh(email)
    return email


async def get_email(db: AsyncSession, tenant_id: str, email_id: uuid.UUID) -> EmailCommunication | None:
    result = await db.execute(select(EmailCommunication).where(EmailCommunication.id == email_id, EmailCommunication.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_email_drafts(db: AsyncSession, tenant_id: str, status: EmailStatus | None = None) -> list[EmailCommunication]:
    stmt = select(EmailCommunication).where(EmailCommunication.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(EmailCommunication.status == status)
    result = await db.execute(stmt.order_by(EmailCommunication.created_at.desc()))
    return list(result.scalars().all())


async def resolve_recipient_count(db: AsyncSession, tenant_id: str, recipient_scope: EmailRecipientScope) -> int:
    """Deterministic — how many people this email will actually reach
    if sent. all_staff counts every active employee; single_employee
    is always exactly one."""
    if recipient_scope == EmailRecipientScope.SINGLE_EMPLOYEE:
        return 1
    result = await db.execute(select(EmployeeProfile).where(EmployeeProfile.tenant_id == tenant_id, EmployeeProfile.employment_status == EmploymentStatus.ACTIVE))
    return len(list(result.scalars().all()))


async def send_email(db: AsyncSession, tenant_id: str, email_id: uuid.UUID) -> EmailCommunication:
    """The sole human-confirmed action that actually dispatches an
    email — the one step neither the manual path nor the AI-drafted
    path can skip."""
    email = await get_email(db, tenant_id, email_id)
    if email is None or email.status != EmailStatus.DRAFT:
        raise EmailNotDraftError("email not found or already sent")
    email.status = EmailStatus.SENT
    email.sent_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(email)
    return email
