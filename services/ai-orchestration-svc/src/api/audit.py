import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import DecisionLog

router = APIRouter(prefix="/api/v1/ai", tags=["audit"])


class DecisionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_key: str
    recommendation_id: uuid.UUID | None
    trigger_event_type: str
    trigger_context: dict
    reasoning: str
    tools_called: list
    action_taken: str
    outcome: str
    is_override: bool
    created_at: datetime


@router.get("/decision-log", response_model=list[DecisionLogOut])
async def list_decision_log(
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    agent_key: str | None = None,
    limit: int = 100,
):
    query = select(DecisionLog).where(DecisionLog.tenant_id == tenant_id)
    if agent_key:
        query = query.where(DecisionLog.agent_key == agent_key)
    result = await db.execute(query.order_by(DecisionLog.created_at.desc()).limit(limit))
    return result.scalars().all()
