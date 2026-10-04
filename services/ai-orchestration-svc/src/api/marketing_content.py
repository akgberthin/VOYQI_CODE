from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import marketing_content_agent

router = APIRouter(prefix="/api/v1/ai/marketing", tags=["marketing-content-agent"])


class GenerateContentRequest(BaseModel):
    campaign_id: str
    campaign_name: str
    channel: str
    brief: str


@router.post("/generate-content")
async def generate_content(
    body: GenerateContentRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    """The synchronous entrypoint a marketing operator's UI calls when
    they click 'AI-draft this' — not event-driven, since content
    generation is normally a deliberate human-initiated request, not a
    reaction to a platform event."""
    result = await marketing_content_agent.generate_content(
        db, tenant_id=tenant_id, campaign_id=body.campaign_id, campaign_name=body.campaign_name,
        channel=body.channel, brief=body.brief,
    )
    return {
        "draft": result.draft,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }
