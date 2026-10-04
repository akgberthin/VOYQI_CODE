import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import media_generation_engine

router = APIRouter(prefix="/api/v1/marketing/media-generation", tags=["media-generation"])


class RequestCreate(BaseModel):
    prompt: str
    media_type: str
    requested_by: str = "marketing-admin"
    provider: str = "unconfigured"


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    prompt: str
    media_type: str
    provider: str
    status: str
    result_url: str | None
    error_detail: str | None
    requested_by: str


@router.post("", response_model=RequestOut, status_code=201)
async def create_request(body: RequestCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Pluggable — this endpoint records the request; fulfillment
    goes through whichever image/video generation MCP App is
    connected for this tenant, selected via the platform's own
    connector directory, not a vendor hardcoded here."""
    return await media_generation_engine.create_request(db, tenant_id, body.prompt, body.media_type, body.requested_by, body.provider)


@router.get("", response_model=list[RequestOut])
async def list_requests(status: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await media_generation_engine.list_requests(db, tenant_id, status)


@router.post("/{request_id}/generating", response_model=RequestOut)
async def mark_generating(request_id: uuid.UUID, provider: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    request = await media_generation_engine.mark_generating(db, tenant_id, request_id, provider)
    if request is None:
        raise HTTPException(status_code=404, detail="Media generation request not found")
    return request


class CompleteRequest(BaseModel):
    result_url: str


@router.post("/{request_id}/complete", response_model=RequestOut)
async def mark_completed(request_id: uuid.UUID, body: CompleteRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    request = await media_generation_engine.mark_completed(db, tenant_id, request_id, body.result_url)
    if request is None:
        raise HTTPException(status_code=404, detail="Media generation request not found")
    return request


class FailRequest(BaseModel):
    error_detail: str


@router.post("/{request_id}/fail", response_model=RequestOut)
async def mark_failed(request_id: uuid.UUID, body: FailRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    request = await media_generation_engine.mark_failed(db, tenant_id, request_id, body.error_detail)
    if request is None:
        raise HTTPException(status_code=404, detail="Media generation request not found")
    return request
