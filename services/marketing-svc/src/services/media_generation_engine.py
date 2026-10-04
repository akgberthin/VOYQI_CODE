"""
Media generation engine — a pluggable adapter interface for image and
video generation, not a hardcoded integration with one vendor.

This service defines the request/result contract (prompt in,
result_url out) and owns the state machine (PENDING -> GENERATING ->
COMPLETED/FAILED). Which actual model fulfills a request — an MCP-
connected image/video generation server, a direct API integration, or
a different provider entirely — is supplied by whatever calls
mark_generating/mark_completed, so the "best" available model can
change over time with zero schema or workflow changes here.

In this platform's own environment, the real fulfillment path is: the
frontend or the orchestrating agent calls out through the MCP
connector directory (search_mcp_registry / suggest_connectors) to
find and invoke a connected image/video generation MCP App, then
reports the result back here via mark_completed. This engine never
picks a provider on its own and never embeds a vendor-specific SDK —
that choice belongs to whichever human or agent invoked the request,
matching the platform's own MCP-App-selection principle that a
provider is never auto-picked for someone who didn't ask for it by
name.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import MediaGenerationRequest, MediaGenerationStatus


async def create_request(db: AsyncSession, tenant_id: str, prompt: str, media_type: str, requested_by: str, provider: str = "unconfigured") -> MediaGenerationRequest:
    request = MediaGenerationRequest(tenant_id=tenant_id, prompt=prompt, media_type=media_type, requested_by=requested_by, provider=provider)
    db.add(request)
    await db.commit()
    await db.refresh(request)
    return request


async def get_request(db: AsyncSession, tenant_id: str, request_id: uuid.UUID) -> MediaGenerationRequest | None:
    result = await db.execute(select(MediaGenerationRequest).where(MediaGenerationRequest.id == request_id, MediaGenerationRequest.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_requests(db: AsyncSession, tenant_id: str, status: str | None = None) -> list[MediaGenerationRequest]:
    query = select(MediaGenerationRequest).where(MediaGenerationRequest.tenant_id == tenant_id)
    if status:
        query = query.where(MediaGenerationRequest.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())


async def mark_generating(db: AsyncSession, tenant_id: str, request_id: uuid.UUID, provider: str) -> MediaGenerationRequest | None:
    request = await get_request(db, tenant_id, request_id)
    if request is None:
        return None
    request.status = MediaGenerationStatus.GENERATING
    request.provider = provider
    await db.commit()
    await db.refresh(request)
    return request


async def mark_completed(db: AsyncSession, tenant_id: str, request_id: uuid.UUID, result_url: str) -> MediaGenerationRequest | None:
    request = await get_request(db, tenant_id, request_id)
    if request is None:
        return None
    request.status = MediaGenerationStatus.COMPLETED
    request.result_url = result_url
    await db.commit()
    await db.refresh(request)
    return request


async def mark_failed(db: AsyncSession, tenant_id: str, request_id: uuid.UUID, error_detail: str) -> MediaGenerationRequest | None:
    request = await get_request(db, tenant_id, request_id)
    if request is None:
        return None
    request.status = MediaGenerationStatus.FAILED
    request.error_detail = error_detail
    await db.commit()
    await db.refresh(request)
    return request
