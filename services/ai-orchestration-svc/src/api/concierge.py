from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import concierge_agent

router = APIRouter(prefix="/api/v1/ai/crm", tags=["concierge-agent"])


class MessageAssessmentRequest(BaseModel):
    message_id: str
    conversation_id: str
    body: str


@router.post("/evaluate-message")
async def evaluate_message(body: MessageAssessmentRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await concierge_agent.evaluate_message(
        db, tenant_id=tenant_id, message_id=body.message_id, conversation_id=body.conversation_id, body=body.body,
    )
    return {
        "sentiment": result.sentiment,
        "intent": result.intent,
        "escalated": result.escalated,
        "draft_reply_id": result.draft_reply_id,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }
