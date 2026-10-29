import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import audience_engine

router = APIRouter(prefix="/api/v1/marketing/audience", tags=["audience"])


class SegmentCreate(BaseModel):
    name: str
    filter_criteria: dict = {}
    created_by: str = "marketing-admin"
    # Fill the new segment with every existing contact matching the criteria.
    populate: bool = False


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    filter_criteria: dict
    member_count: int
    created_by: str


@router.post("/segments", response_model=SegmentOut, status_code=201)
async def create_segment(body: SegmentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        audience_engine.check_criteria(body.filter_criteria)
    except audience_engine.InvalidCriteria as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    segment = await audience_engine.create_segment(db, tenant_id, body.name, body.filter_criteria, body.created_by)
    if body.populate:
        await audience_engine.populate(db, tenant_id, segment.id, body.filter_criteria)
        await db.refresh(segment)
    return segment


class EstimateIn(BaseModel):
    filter_criteria: dict = {}


@router.post("/estimate")
async def estimate(body: EstimateIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """How many of the tenant's contacts match, and how many of those each
    channel can reach (voice: only with consent to automated calls)."""
    try:
        return await audience_engine.estimate(db, tenant_id, body.filter_criteria)
    except audience_engine.InvalidCriteria as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/attributes")
async def attributes(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await audience_engine.attribute_values(db, tenant_id)


@router.get("/segments", response_model=list[SegmentOut])
async def list_segments(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await audience_engine.list_segments(db, tenant_id)


class MemberCountUpdate(BaseModel):
    member_count: int


@router.patch("/segments/{segment_id}/member-count", response_model=SegmentOut)
async def set_member_count(segment_id: uuid.UUID, body: MemberCountUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    segment = await audience_engine.set_member_count(db, tenant_id, segment_id, body.member_count)
    if segment is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    return segment


class MemberIn(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    push_token: str = ""
    consent_voice: bool = False
    attributes: dict = {}


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    email: str
    phone: str
    push_token: str
    consent_voice: bool
    attributes: dict = {}


class MembersImport(BaseModel):
    members: list[MemberIn]


@router.post("/segments/{segment_id}/members")
async def add_members(segment_id: uuid.UUID, body: MembersImport, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Bulk import contacts; ones already in the segment are skipped."""
    try:
        added = await audience_engine.add_members(db, tenant_id, segment_id, [m.model_dump() for m in body.members])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"added": added, "skipped": len(body.members) - added}


@router.get("/segments/{segment_id}/members", response_model=list[MemberOut])
async def list_members(segment_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await audience_engine.list_members(db, tenant_id, segment_id)


@router.delete("/segments/{segment_id}/members/{member_id}", status_code=204)
async def remove_member(segment_id: uuid.UUID, member_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    if not await audience_engine.remove_member(db, tenant_id, segment_id, member_id):
        raise HTTPException(status_code=404, detail="Member not found")
