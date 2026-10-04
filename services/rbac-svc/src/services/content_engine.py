"""
SAP-006 — Page content management engine (CMS).

Fully autonomous: editing and publishing page content is a content
operation, not an access-control decision. There's no approval gate
on publishing — the same principle as settings_engine.py.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import PageContent, PageContentStatus


async def upsert_page(
    db: AsyncSession, tenant_id: str, page_key: str, title: str, body: str, updated_by: str,
    slug: str = "", seo_title: str = "", seo_description: str = "", template: str = "default",
) -> PageContent:
    result = await db.execute(select(PageContent).where(PageContent.tenant_id == tenant_id, PageContent.page_key == page_key))
    existing = result.scalar_one_or_none()
    if existing:
        existing.title = title
        existing.body = body
        existing.slug = slug or existing.slug
        existing.seo_title = seo_title or existing.seo_title
        existing.seo_description = seo_description or existing.seo_description
        existing.template = template or existing.template
        existing.updated_by = updated_by
        await db.commit()
        await db.refresh(existing)
        return existing

    page = PageContent(
        tenant_id=tenant_id, page_key=page_key, title=title, body=body, updated_by=updated_by,
        slug=slug or page_key, seo_title=seo_title or title, seo_description=seo_description, template=template,
    )
    db.add(page)
    await db.commit()
    await db.refresh(page)
    return page


async def list_pages(db: AsyncSession, tenant_id: str) -> list[PageContent]:
    result = await db.execute(select(PageContent).where(PageContent.tenant_id == tenant_id))
    return list(result.scalars().all())


async def get_page(db: AsyncSession, tenant_id: str, page_id: uuid.UUID) -> PageContent | None:
    result = await db.execute(select(PageContent).where(PageContent.id == page_id, PageContent.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def publish_page(db: AsyncSession, tenant_id: str, page_id: uuid.UUID) -> PageContent | None:
    page = await get_page(db, tenant_id, page_id)
    if page is None:
        return None
    page.status = PageContentStatus.PUBLISHED
    await db.commit()
    await db.refresh(page)
    return page


async def unpublish_page(db: AsyncSession, tenant_id: str, page_id: uuid.UUID) -> PageContent | None:
    page = await get_page(db, tenant_id, page_id)
    if page is None:
        return None
    page.status = PageContentStatus.DRAFT
    await db.commit()
    await db.refresh(page)
    return page


async def delete_page(db: AsyncSession, tenant_id: str, page_id: uuid.UUID) -> bool:
    page = await get_page(db, tenant_id, page_id)
    if page is None:
        return False
    await db.delete(page)
    await db.commit()
    return True
