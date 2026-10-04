"""
SAP-006 — Website section & subsection engine.

Fully autonomous: creating, editing, reordering, and toggling
visibility of a section or subsection is a content-structure
operation, not an access-control decision. Every level (section,
subsection) has an independent visible flag — hiding a subsection
never touches its parent section's visibility or any sibling, and
vice versa.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Section, SubSection


# ══════════════════════════════════════════════════════════════
# Sections
# ══════════════════════════════════════════════════════════════
async def create_section(
    db: AsyncSession, tenant_id: str, page_id: uuid.UUID, section_key: str, section_type: str,
    title: str, updated_by: str, order_index: int | None = None,
) -> Section:
    if order_index is None:
        existing = await list_sections(db, tenant_id, page_id)
        order_index = len(existing)
    section = Section(tenant_id=tenant_id, page_id=page_id, section_key=section_key, section_type=section_type, title=title, order_index=order_index, updated_by=updated_by)
    db.add(section)
    await db.commit()
    await db.refresh(section)
    return section


async def list_sections(db: AsyncSession, tenant_id: str, page_id: uuid.UUID) -> list[Section]:
    result = await db.execute(select(Section).where(Section.tenant_id == tenant_id, Section.page_id == page_id).order_by(Section.order_index))
    return list(result.scalars().all())


async def get_section(db: AsyncSession, tenant_id: str, section_id: uuid.UUID) -> Section | None:
    result = await db.execute(select(Section).where(Section.id == section_id, Section.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def update_section(db: AsyncSession, tenant_id: str, section_id: uuid.UUID, updates: dict, updated_by: str) -> Section | None:
    """A single generic update path for every editable field —
    title, visibility, parallax settings — all go through here so
    there's exactly one place that stamps updated_by/updated_at."""
    section = await get_section(db, tenant_id, section_id)
    if section is None:
        return None
    for key, value in updates.items():
        if hasattr(section, key):
            setattr(section, key, value)
    section.updated_by = updated_by
    await db.commit()
    await db.refresh(section)
    return section


async def reorder_section(db: AsyncSession, tenant_id: str, section_id: uuid.UUID, new_order_index: int) -> Section | None:
    return await update_section(db, tenant_id, section_id, {"order_index": new_order_index}, "system")


async def delete_section(db: AsyncSession, tenant_id: str, section_id: uuid.UUID) -> bool:
    """Deleting a section cascades to delete its subsections too —
    a subsection with no parent section isn't meaningfully addressable
    from any page."""
    section = await get_section(db, tenant_id, section_id)
    if section is None:
        return False
    subs = await list_subsections(db, tenant_id, section_id)
    for sub in subs:
        await db.delete(sub)
    await db.delete(section)
    await db.commit()
    return True


# ══════════════════════════════════════════════════════════════
# Subsections
# ══════════════════════════════════════════════════════════════
async def create_subsection(
    db: AsyncSession, tenant_id: str, section_id: uuid.UUID, subsection_key: str, block_type: str,
    content: str, updated_by: str, image_url: str = "", order_index: int | None = None,
) -> SubSection:
    if order_index is None:
        existing = await list_subsections(db, tenant_id, section_id)
        order_index = len(existing)
    sub = SubSection(tenant_id=tenant_id, section_id=section_id, subsection_key=subsection_key, block_type=block_type, content=content, image_url=image_url, order_index=order_index, updated_by=updated_by)
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


async def list_subsections(db: AsyncSession, tenant_id: str, section_id: uuid.UUID) -> list[SubSection]:
    result = await db.execute(select(SubSection).where(SubSection.tenant_id == tenant_id, SubSection.section_id == section_id).order_by(SubSection.order_index))
    return list(result.scalars().all())


async def get_subsection(db: AsyncSession, tenant_id: str, subsection_id: uuid.UUID) -> SubSection | None:
    result = await db.execute(select(SubSection).where(SubSection.id == subsection_id, SubSection.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def update_subsection(db: AsyncSession, tenant_id: str, subsection_id: uuid.UUID, updates: dict, updated_by: str) -> SubSection | None:
    sub = await get_subsection(db, tenant_id, subsection_id)
    if sub is None:
        return None
    for key, value in updates.items():
        if hasattr(sub, key):
            setattr(sub, key, value)
    sub.updated_by = updated_by
    await db.commit()
    await db.refresh(sub)
    return sub


async def delete_subsection(db: AsyncSession, tenant_id: str, subsection_id: uuid.UUID) -> bool:
    sub = await get_subsection(db, tenant_id, subsection_id)
    if sub is None:
        return False
    await db.delete(sub)
    await db.commit()
    return True
