import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import FundingSourceStatus, FundingSourceType
from src.services import funding_source_engine

router = APIRouter(prefix="/api/v1/capital-finance/funding-sources", tags=["funding-sources"])


class FundingSourceIn(BaseModel):
    key: str
    name: str
    source_type: FundingSourceType


class FundingSourceOut(BaseModel):
    id: str
    key: str
    name: str
    source_type: FundingSourceType
    status: FundingSourceStatus


def _to_out(f) -> FundingSourceOut:
    return FundingSourceOut(id=str(f.id), key=f.key, name=f.name, source_type=f.source_type, status=f.status)


@router.post("", response_model=FundingSourceOut, status_code=201)
async def create_funding_source(
    payload: FundingSourceIn,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> FundingSourceOut:
    source = await funding_source_engine.create_funding_source(
        db, tenant_id, payload.key, payload.name, payload.source_type
    )
    return _to_out(source)


@router.get("", response_model=list[FundingSourceOut])
async def list_funding_sources(
    source_type: FundingSourceType | None = None,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[FundingSourceOut]:
    sources = await funding_source_engine.list_funding_sources(db, tenant_id, source_type)
    return [_to_out(s) for s in sources]


@router.delete("/{funding_source_id}", response_model=FundingSourceOut)
async def deactivate_funding_source(
    funding_source_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> FundingSourceOut:
    source = await funding_source_engine.deactivate_funding_source(db, tenant_id, funding_source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Funding source not found")
    return _to_out(source)
