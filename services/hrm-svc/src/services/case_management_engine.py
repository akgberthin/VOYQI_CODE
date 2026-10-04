"""
HRM-007 Employee Self-Service & Case Management.

Complaints ALWAYS escalate automatically and never receive an AI-drafted
reply — a deterministic, case-type-based rule, not a judgment call. This
mirrors the Concierge Agent's escalation discipline exactly: sensitive
topics get a human, not a draft. Policy questions are the genuine
AI-first capability: create_case_draft_reply always writes
PENDING_REVIEW; approve_and_send_reply is the sole human-confirmed
action that actually sends anything.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    CaseDraftReply,
    CaseDraftReplyStatus,
    CaseMessage,
    CaseMessageDirection,
    CaseStatus,
    CaseType,
    HRCase,
    PolicyDocument,
)


class DraftReplyNotPendingError(ValueError):
    pass


# SLA hours by case type — a real, defensible policy: complaints get
# the tightest turnaround since they're the most consequential to leave
# unaddressed; document requests are usually just paperwork.
_SLA_HOURS_BY_CASE_TYPE = {
    CaseType.COMPLAINT: 24,
    CaseType.PAYROLL_INQUIRY: 48,
    CaseType.BENEFITS_QUESTION: 72,
    CaseType.POLICY_QUESTION: 72,
    CaseType.DOCUMENT_REQUEST: 48,
}

# Case types that always escalate — never receive an AI-drafted reply,
# the same principle as Concierge Agent's sensitive-topic escalation.
_ALWAYS_ESCALATE_CASE_TYPES = {CaseType.COMPLAINT}


async def create_case(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, case_type: CaseType, subject: str) -> HRCase:
    sla_hours = _SLA_HOURS_BY_CASE_TYPE.get(case_type, 72)
    sla_due_at = datetime.now(timezone.utc) + timedelta(hours=sla_hours)
    status = CaseStatus.ESCALATED if case_type in _ALWAYS_ESCALATE_CASE_TYPES else CaseStatus.OPEN
    case = HRCase(tenant_id=tenant_id, employee_id=employee_id, case_type=case_type, subject=subject, sla_due_at=sla_due_at, status=status)
    db.add(case)
    await db.commit()
    await db.refresh(case)
    return case


async def get_case(db: AsyncSession, tenant_id: str, case_id: uuid.UUID) -> HRCase | None:
    result = await db.execute(select(HRCase).where(HRCase.id == case_id, HRCase.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_cases(db: AsyncSession, tenant_id: str, status: CaseStatus | None = None) -> list[HRCase]:
    stmt = select(HRCase).where(HRCase.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(HRCase.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_case_status(db: AsyncSession, tenant_id: str, case_id: uuid.UUID, status: CaseStatus) -> HRCase | None:
    case = await get_case(db, tenant_id, case_id)
    if case is None:
        return None
    case.status = status
    await db.commit()
    await db.refresh(case)
    return case


def is_case_sla_breached(case: HRCase, as_of: datetime | None = None) -> bool:
    as_of = as_of or datetime.now(timezone.utc)
    sla_due_at = case.sla_due_at if case.sla_due_at.tzinfo else case.sla_due_at.replace(tzinfo=timezone.utc)
    return case.status not in (CaseStatus.RESOLVED,) and as_of > sla_due_at


# ── Case messages (facts only) ──────────────────────────────────────────

async def add_message(db: AsyncSession, tenant_id: str, case_id: uuid.UUID, direction: CaseMessageDirection, body: str, sender: str = "") -> CaseMessage:
    message = CaseMessage(tenant_id=tenant_id, case_id=case_id, direction=direction, body=body, sender=sender)
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


async def list_messages(db: AsyncSession, tenant_id: str, case_id: uuid.UUID) -> list[CaseMessage]:
    result = await db.execute(select(CaseMessage).where(CaseMessage.tenant_id == tenant_id, CaseMessage.case_id == case_id).order_by(CaseMessage.created_at))
    return list(result.scalars().all())


# ── Policy documents (grounding source, configured facts) ──────────────

async def create_policy_document(db: AsyncSession, tenant_id: str, title: str, body: str, category: str = "") -> PolicyDocument:
    doc = PolicyDocument(tenant_id=tenant_id, title=title, body=body, category=category)
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def list_policy_documents(db: AsyncSession, tenant_id: str, category: str | None = None) -> list[PolicyDocument]:
    stmt = select(PolicyDocument).where(PolicyDocument.tenant_id == tenant_id)
    if category:
        stmt = stmt.where(PolicyDocument.category == category)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_policy_document(db: AsyncSession, tenant_id: str, policy_id: uuid.UUID) -> PolicyDocument | None:
    result = await db.execute(select(PolicyDocument).where(PolicyDocument.id == policy_id, PolicyDocument.tenant_id == tenant_id))
    return result.scalar_one_or_none()


# ── Case draft replies (Talent Agent output, human-confirmed send) ────

async def create_case_draft_reply(db: AsyncSession, tenant_id: str, case_id: uuid.UUID, draft_body: str, reasoning: str = "", cites_policy_id: uuid.UUID | None = None) -> CaseDraftReply:
    draft = CaseDraftReply(tenant_id=tenant_id, case_id=case_id, draft_body=draft_body, reasoning=reasoning, cites_policy_id=cites_policy_id)
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def list_pending_case_drafts(db: AsyncSession, tenant_id: str, case_id: uuid.UUID | None = None) -> list[CaseDraftReply]:
    stmt = select(CaseDraftReply).where(CaseDraftReply.tenant_id == tenant_id, CaseDraftReply.status == CaseDraftReplyStatus.PENDING_REVIEW)
    if case_id:
        stmt = stmt.where(CaseDraftReply.case_id == case_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def approve_and_send_reply(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID, final_body: str | None = None) -> tuple[CaseDraftReply, CaseMessage]:
    result = await db.execute(select(CaseDraftReply).where(CaseDraftReply.id == draft_id, CaseDraftReply.tenant_id == tenant_id))
    draft = result.scalar_one_or_none()
    if draft is None or draft.status != CaseDraftReplyStatus.PENDING_REVIEW:
        raise DraftReplyNotPendingError("draft not found or already reviewed")

    body_to_send = final_body if final_body is not None else draft.draft_body
    message = await add_message(db, tenant_id, draft.case_id, CaseMessageDirection.OUTBOUND, body_to_send, sender="hr-agent-reviewed")
    draft.status = CaseDraftReplyStatus.APPROVED_SENT
    draft.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return draft, message


async def reject_case_draft(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID) -> CaseDraftReply | None:
    result = await db.execute(select(CaseDraftReply).where(CaseDraftReply.id == draft_id, CaseDraftReply.tenant_id == tenant_id))
    draft = result.scalar_one_or_none()
    if draft is None or draft.status != CaseDraftReplyStatus.PENDING_REVIEW:
        return None
    draft.status = CaseDraftReplyStatus.REJECTED
    draft.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(draft)
    return draft
