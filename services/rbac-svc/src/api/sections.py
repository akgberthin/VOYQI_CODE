import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import audit_engine, section_engine

router = APIRouter(prefix="/api/v1/rbac/sections", tags=["sections"])


class SectionCreate(BaseModel):
    page_id: uuid.UUID
    section_key: str
    section_type: str = "custom"
    title: str = ""
    updated_by: str = "super-admin"


class SectionUpdate(BaseModel):
    title: str | None = None
    visible: bool | None = None
    parallax_enabled: bool | None = None
    parallax_speed: float | None = None
    parallax_image_url: str | None = None
    order_index: int | None = None
    updated_by: str = "super-admin"


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    page_id: uuid.UUID
    section_key: str
    section_type: str
    title: str
    order_index: int
    visible: bool
    parallax_enabled: bool
    parallax_speed: float
    parallax_image_url: str
    updated_by: str


@router.post("", response_model=SectionOut, status_code=201)
async def create_section(body: SectionCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    section = await section_engine.create_section(db, tenant_id, body.page_id, body.section_key, body.section_type, body.title, body.updated_by)
    await audit_engine.record(db, tenant_id, "human", body.updated_by, "section.created", body.section_key)
    return section


@router.get("", response_model=list[SectionOut])
async def list_sections(page_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await section_engine.list_sections(db, tenant_id, page_id)


@router.patch("/{section_id}", response_model=SectionOut)
async def update_section(section_id: uuid.UUID, body: SectionUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Single generic update path — used for renaming, hiding,
    reordering, and every parallax field, so toggling one thing (e.g.
    visibility) never requires resending the whole section."""
    updates = {k: v for k, v in body.model_dump(exclude={"updated_by"}).items() if v is not None}
    section = await section_engine.update_section(db, tenant_id, section_id, updates, body.updated_by)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found")
    await audit_engine.record(db, tenant_id, "human", body.updated_by, "section.updated", str(section_id), str(updates))
    return section


@router.delete("/{section_id}")
async def delete_section(section_id: uuid.UUID, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Cascades to delete every subsection beneath it."""
    deleted = await section_engine.delete_section(db, tenant_id, section_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Section not found")
    await audit_engine.record(db, tenant_id, "human", actor_id, "section.deleted", str(section_id))
    return {"deleted": True}


# ══════════════════════════════════════════════════════════════
# Subsections
# ══════════════════════════════════════════════════════════════
class SubSectionCreate(BaseModel):
    section_id: uuid.UUID
    subsection_key: str
    block_type: str = "text"
    content: str = ""
    image_url: str = ""
    updated_by: str = "super-admin"


class SubSectionUpdate(BaseModel):
    content: str | None = None
    image_url: str | None = None
    visible: bool | None = None
    order_index: int | None = None
    updated_by: str = "super-admin"


class SubSectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    section_id: uuid.UUID
    subsection_key: str
    block_type: str
    content: str
    image_url: str
    order_index: int
    visible: bool
    updated_by: str


@router.post("/subsections", response_model=SubSectionOut, status_code=201)
async def create_subsection(body: SubSectionCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    sub = await section_engine.create_subsection(db, tenant_id, body.section_id, body.subsection_key, body.block_type, body.content, body.updated_by, body.image_url)
    await audit_engine.record(db, tenant_id, "human", body.updated_by, "subsection.created", body.subsection_key)
    return sub


@router.get("/subsections", response_model=list[SubSectionOut])
async def list_subsections(section_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await section_engine.list_subsections(db, tenant_id, section_id)


@router.patch("/subsections/{subsection_id}", response_model=SubSectionOut)
async def update_subsection(subsection_id: uuid.UUID, body: SubSectionUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    updates = {k: v for k, v in body.model_dump(exclude={"updated_by"}).items() if v is not None}
    sub = await section_engine.update_subsection(db, tenant_id, subsection_id, updates, body.updated_by)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subsection not found")
    await audit_engine.record(db, tenant_id, "human", body.updated_by, "subsection.updated", str(subsection_id), str(updates))
    return sub


@router.delete("/subsections/{subsection_id}")
async def delete_subsection(subsection_id: uuid.UUID, actor_id: str = "super-admin", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    deleted = await section_engine.delete_subsection(db, tenant_id, subsection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Subsection not found")
    await audit_engine.record(db, tenant_id, "human", actor_id, "subsection.deleted", str(subsection_id))
    return {"deleted": True}
