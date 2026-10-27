import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import ContentStatus
from src.services import ai_drafting, campaign_engine, content_engine

router = APIRouter(prefix="/api/v1/marketing/content", tags=["content"])


class ContentDraftCreate(BaseModel):
    campaign_id: uuid.UUID
    channel: str
    body_text: str
    subject_line: str = ""
    media_asset_ids: list[str] = []
    created_by: str = "marketing-admin"


class ContentDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    campaign_id: uuid.UUID
    channel: str
    subject_line: str
    body_text: str
    media_asset_ids: list
    status: str
    guardrail_findings: list
    reviewed_by: str | None
    created_by: str


@router.post("", response_model=ContentDraftOut, status_code=201)
async def create_content_draft(body: ContentDraftCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous: the guardrail check and the resulting
    APPROVED/HELD_FOR_REVIEW status happen in this same call."""
    return await content_engine.create_content_draft(db, tenant_id, body.campaign_id, body.channel, body.body_text, body.created_by, body.subject_line, body.media_asset_ids)


class AIDraftRequest(BaseModel):
    campaign_id: uuid.UUID
    brief: str = ""


@router.post("/ai-draft")
async def request_ai_draft(body: AIDraftRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """'AI-draft this' for a campaign: the Marketing Content Agent writes a
    draft for the campaign's channel; it lands as an ordinary content draft
    (approved, or held if it fails the guardrail)."""
    campaign = await campaign_engine.get_campaign(db, tenant_id, body.campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    try:
        result = await ai_drafting.generate(tenant_id, str(campaign.id), campaign.name, campaign.channel.value if hasattr(campaign.channel, "value") else str(campaign.channel), body.brief or campaign.objective)
    except ai_drafting.AIDraftUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result.get("skipped_reason"):
        raise HTTPException(status_code=409, detail=f"Marketing Content Agent is {result['skipped_reason']} — resume it in the super admin console")
    return result


class ComposeIn(BaseModel):
    channel: str
    mobility_class: str = ""
    objective: str = "acquisition"
    tones: list[str] = ["premium", "personal", "urgent"]
    context: str = ""


@router.post("/compose")
async def compose(body: ComposeIn, tenant_id: str = Depends(get_tenant_id)):
    """AI studio: copy variants (one per tone) from the Marketing Content
    Agent to choose from — nothing is drafted until one is used."""
    try:
        result = await ai_drafting.compose(tenant_id, body.model_dump())
    except ai_drafting.AIDraftUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result.get("skipped_reason"):
        raise HTTPException(status_code=409, detail=f"Marketing Content Agent is {result['skipped_reason']} — resume it in the super admin console")
    return result


@router.get("", response_model=list[ContentDraftOut])
async def list_content_drafts(campaign_id: uuid.UUID | None = None, status: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await content_engine.list_content_drafts(db, tenant_id, campaign_id, status)


@router.get("/{draft_id}", response_model=ContentDraftOut)
async def get_content_draft(draft_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    draft = await content_engine.get_content_draft(db, tenant_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Content draft not found")
    return draft


class ReviewRequest(BaseModel):
    reviewed_by: str


@router.post("/{draft_id}/approve", response_model=ContentDraftOut)
async def human_approve_held_draft(draft_id: uuid.UUID, body: ReviewRequest, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The one human action this service actually needs: clearing a
    draft the guardrail held — not a default review step. Overriding
    the guardrail is published for the Compliance/Audit Agent."""
    existing = await content_engine.get_content_draft(db, tenant_id, draft_id)
    was_held = existing is not None and existing.status == ContentStatus.HELD_FOR_REVIEW
    draft = await content_engine.human_approve_held_draft(db, tenant_id, draft_id, body.reviewed_by)
    if draft is None:
        raise HTTPException(status_code=404, detail="Content draft not found")
    if was_held and draft.status == ContentStatus.APPROVED:
        background_tasks.add_task(
            publish_event, "marketing.content", "content_draft.approved_with_violations", tenant_id,
            {"draft_id": str(draft.id), "violation_count": len(draft.guardrail_findings or []), "findings": list(draft.guardrail_findings or []), "reviewed_by": body.reviewed_by},
        )
    return draft


@router.post("/{draft_id}/reject", response_model=ContentDraftOut)
async def reject_draft(draft_id: uuid.UUID, body: ReviewRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    draft = await content_engine.reject_draft(db, tenant_id, draft_id, body.reviewed_by)
    if draft is None:
        raise HTTPException(status_code=404, detail="Content draft not found")
    return draft
