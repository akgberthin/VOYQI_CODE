"""
HRM-008 Notices, Communication & Employee Notes.

Notices and comments are entirely deterministic facts — the notice
board itself IS the "app" delivery channel; email/sms on a notice
record intent only, since this platform has no integrated SMS/email
gateway.

Employee notes are the one place on this platform where an AI-authored
write doesn't require human approval before landing — logging that a
case was resolved or a review was finalized is documentation, not a
decision. The hard rule that still applies: append_to_note can only
ever add text, never replace or delete what's already there. A human's
own words are never at risk of being silently rewritten by an agent.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import EmployeeNote, Notice, NoticeComment

# ── Notices (deterministic facts) ───────────────────────────────────────

async def create_notice(db: AsyncSession, tenant_id: str, title: str, body: str, posted_by: str = "", channels: str = "app") -> Notice:
    notice = Notice(tenant_id=tenant_id, title=title, body=body, posted_by=posted_by, channels=channels)
    db.add(notice)
    await db.commit()
    await db.refresh(notice)
    return notice


async def list_notices(db: AsyncSession, tenant_id: str) -> list[Notice]:
    result = await db.execute(select(Notice).where(Notice.tenant_id == tenant_id).order_by(Notice.created_at.desc()))
    return list(result.scalars().all())


async def get_notice(db: AsyncSession, tenant_id: str, notice_id: uuid.UUID) -> Notice | None:
    result = await db.execute(select(Notice).where(Notice.id == notice_id, Notice.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def add_notice_comment(db: AsyncSession, tenant_id: str, notice_id: uuid.UUID, employee_id: uuid.UUID, body: str) -> NoticeComment:
    comment = NoticeComment(tenant_id=tenant_id, notice_id=notice_id, employee_id=employee_id, body=body)
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


async def list_notice_comments(db: AsyncSession, tenant_id: str, notice_id: uuid.UUID) -> list[NoticeComment]:
    result = await db.execute(select(NoticeComment).where(NoticeComment.tenant_id == tenant_id, NoticeComment.notice_id == notice_id).order_by(NoticeComment.created_at))
    return list(result.scalars().all())


# ── Employee notes (human or AI can add; AI can only append, never
#    overwrite) ─────────────────────────────────────────────────────────

async def add_note(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, body: str, author: str = "", is_ai_generated: bool = False) -> EmployeeNote:
    note = EmployeeNote(tenant_id=tenant_id, employee_id=employee_id, body=body, author=author, is_ai_generated=is_ai_generated)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


async def list_notes_for_employee(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> list[EmployeeNote]:
    result = await db.execute(select(EmployeeNote).where(EmployeeNote.tenant_id == tenant_id, EmployeeNote.employee_id == employee_id).order_by(EmployeeNote.created_at.desc()))
    return list(result.scalars().all())


async def get_most_recent_note(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> EmployeeNote | None:
    result = await db.execute(
        select(EmployeeNote).where(EmployeeNote.tenant_id == tenant_id, EmployeeNote.employee_id == employee_id).order_by(EmployeeNote.created_at.desc())
    )
    return result.scalars().first()


async def append_to_note(db: AsyncSession, tenant_id: str, note_id: uuid.UUID, additional_text: str, author: str = "") -> EmployeeNote | None:
    """The one function that ever modifies an existing note — and it
    only ever adds a clearly attributed, timestamped segment. The
    original body is never truncated, edited, or removed."""
    result = await db.execute(select(EmployeeNote).where(EmployeeNote.id == note_id, EmployeeNote.tenant_id == tenant_id))
    note = result.scalar_one_or_none()
    if note is None:
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    attribution = author or "unattributed"
    note.body = f"{note.body}\n\n--- Update {timestamp} by {attribution} ---\n{additional_text}"
    note.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(note)
    return note
