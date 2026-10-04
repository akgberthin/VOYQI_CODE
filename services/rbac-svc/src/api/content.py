import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import audit_engine, content_engine

router = APIRouter(prefix="/api/v1/rbac/page-content", tags=["content"])


class PageUpsert(BaseModel):
    page_key: str
    title: str
    body: str = ""
    slug: str = ""
    seo_title: str = ""
    seo_description: str = ""
    template: str = "default"
    updated_by: str = "super-admin"


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    page_key: str
    title: str
    slug: str
    seo_title: str
    seo_description: str
    template: str
    body: str
    status: str
    updated_by: str


@router.post("", response_model=PageOut, status_code=201)
async def upsert_page(body: PageUpsert, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    page = await content_engine.upsert_page(db, tenant_id, body.page_key, body.title, body.body, body.updated_by, body.slug, body.seo_title, body.seo_description, body.template)
    await audit_engine.record(db, tenant_id, "human", body.updated_by, "page_content.updated", body.page_key)
    return page


@router.get("", response_model=list[PageOut])
async def list_pages(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await content_engine.list_pages(db, tenant_id)


@router.get("/{page_id}", response_model=PageOut)
async def get_page(page_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    page = await content_engine.get_page(db, tenant_id, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@router.post("/{page_id}/publish", response_model=PageOut)
async def publish_page(page_id: uuid.UUID, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    page = await content_engine.publish_page(db, tenant_id, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    await audit_engine.record(db, tenant_id, "human", actor_id, "page_content.published", page.page_key)
    return page


@router.post("/{page_id}/unpublish", response_model=PageOut)
async def unpublish_page(page_id: uuid.UUID, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    page = await content_engine.unpublish_page(db, tenant_id, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    await audit_engine.record(db, tenant_id, "human", actor_id, "page_content.unpublished", page.page_key)
    return page


@router.delete("/{page_id}")
async def delete_page(page_id: uuid.UUID, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    deleted = await content_engine.delete_page(db, tenant_id, page_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Page not found")
    await audit_engine.record(db, tenant_id, "human", actor_id, "page_content.deleted", str(page_id))
    return {"deleted": True}
