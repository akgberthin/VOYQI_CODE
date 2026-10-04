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


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    filter_criteria: dict
    member_count: int
    created_by: str


@router.post("/segments", response_model=SegmentOut, status_code=201)
async def create_segment(body: SegmentCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await audience_engine.create_segment(db, tenant_id, body.name, body.filter_criteria, body.created_by)


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
