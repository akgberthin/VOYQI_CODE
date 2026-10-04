import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import Agent, AgentStatus

router = APIRouter(prefix="/api/v1/ai", tags=["agent-operations"])


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    display_name: str
    status: AgentStatus
    model: str
    model_pinned: bool
    human_approval_threshold: int
    decisions_made: int
    recommendations_pending: int


class AgentRegister(BaseModel):
    key: str
    display_name: str
    description: str = ""
    human_approval_threshold: int = 50
    model: str = "claude-sonnet-5"


class AgentStatusUpdate(BaseModel):
    status: AgentStatus


@router.post("/agents", response_model=AgentOut, status_code=201)
async def register_agent(
    body: AgentRegister, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    agent = Agent(tenant_id=tenant_id, **body.model_dump())
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.tenant_id == tenant_id))
    return result.scalars().all()


@router.post("/agents/{agent_id}/status", response_model=AgentOut)
async def set_agent_status(
    agent_id: uuid.UUID,
    body: AgentStatusUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Pause/resume/disable — the human override switch every agent must have."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.status = body.status
    await db.commit()
    await db.refresh(agent)
    return agent
