"""Campaign notes engine — internal-only, never sent anywhere. Fully
autonomous CRUD."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Note


async def create_note(db: AsyncSession, tenant_id: str, author: str, body: str, campaign_id: uuid.UUID | None = None) -> Note:
    note = Note(tenant_id=tenant_id, author=author, body=body, campaign_id=campaign_id)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


async def list_notes(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID | None = None) -> list[Note]:
    query = select(Note).where(Note.tenant_id == tenant_id)
    if campaign_id:
        query = query.where(Note.campaign_id == campaign_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def delete_note(db: AsyncSession, tenant_id: str, note_id: uuid.UUID) -> bool:
    result = await db.execute(select(Note).where(Note.id == note_id, Note.tenant_id == tenant_id))
    note = result.scalar_one_or_none()
    if note is None:
        return False
    await db.delete(note)
    await db.commit()
    return True
