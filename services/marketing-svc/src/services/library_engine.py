"""Content library engine — reusable, channel-tagged content blocks.
Fully autonomous CRUD; not customer-facing until pulled into a
campaign's own ContentDraft, which is where the guardrail applies."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ContentLibraryItem


async def create_library_item(db: AsyncSession, tenant_id: str, title: str, channel: str, body_text: str, created_by: str, tags: list[str] | None = None) -> ContentLibraryItem:
    item = ContentLibraryItem(tenant_id=tenant_id, title=title, channel=channel, body_text=body_text, created_by=created_by, tags=tags or [])
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def list_library_items(db: AsyncSession, tenant_id: str, channel: str | None = None) -> list[ContentLibraryItem]:
    query = select(ContentLibraryItem).where(ContentLibraryItem.tenant_id == tenant_id)
    if channel:
        query = query.where(ContentLibraryItem.channel == channel)
    result = await db.execute(query)
    return list(result.scalars().all())


async def delete_library_item(db: AsyncSession, tenant_id: str, item_id: uuid.UUID) -> bool:
    result = await db.execute(select(ContentLibraryItem).where(ContentLibraryItem.id == item_id, ContentLibraryItem.tenant_id == tenant_id))
    item = result.scalar_one_or_none()
    if item is None:
        return False
    await db.delete(item)
    await db.commit()
    return True
