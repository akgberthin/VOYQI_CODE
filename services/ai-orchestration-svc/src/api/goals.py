import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_current_actor, get_tenant_id
from src.models import GoalState, GoalStateHistory

router = APIRouter(prefix="/api/v1/ai", tags=["goals"])


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_key: str
    goal_key: str
    target_value: float
    unit: str
    updated_by: str


class GoalCreate(BaseModel):
    agent_key: str
    goal_key: str
    target_value: float
    unit: str = ""


class GoalUpdate(BaseModel):
    target_value: float


@router.post("/goals", response_model=GoalOut, status_code=201)
async def create_goal(
    body: GoalCreate,
    tenant_id: str = Depends(get_tenant_id),
    actor: str = Depends(get_current_actor),
    db: AsyncSession = Depends(get_db),
):
    goal = GoalState(tenant_id=tenant_id, updated_by=actor, **body.model_dump())
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return goal


@router.get("/goals", response_model=list[GoalOut])
async def list_goals(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GoalState).where(GoalState.tenant_id == tenant_id))
    return result.scalars().all()


@router.put("/goals/{goal_id}", response_model=GoalOut)
async def update_goal(
    goal_id: uuid.UUID,
    body: GoalUpdate,
    tenant_id: str = Depends(get_tenant_id),
    actor: str = Depends(get_current_actor),
    db: AsyncSession = Depends(get_db),
):
    """Immediate recalibration: the agent reads GoalState fresh on every
    reasoning pass, so this write takes effect on the very next decision —
    no redeploy, no cache to bust."""
    result = await db.execute(
        select(GoalState).where(GoalState.id == goal_id, GoalState.tenant_id == tenant_id)
    )
    goal = result.scalar_one_or_none()
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")

    db.add(
        GoalStateHistory(
            tenant_id=tenant_id,
            goal_state_id=goal.id,
            previous_target_value=goal.target_value,
            new_target_value=body.target_value,
            changed_by=actor,
        )
    )
    goal.target_value = body.target_value
    goal.updated_by = actor
    goal.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(goal)
    return goal
