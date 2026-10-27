import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.roster import ROSTER
from src.core.security import get_tenant_id
from src.models import Agent, AgentStatus, DecisionLog
from src.services import compliance_audit_agent

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
    """Pause/resume/disable — the human override switch every agent must have.

    Turning an agent off (or back on) is itself audited: the
    Compliance/Audit Agent evaluates feature_flag.kill_switch_toggled
    directly rather than through the supervisor, so that disabling the
    Compliance/Audit Agent itself is still recorded."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return await _apply_status(db, tenant_id, agent, body.status)


async def _apply_status(db: AsyncSession, tenant_id: str, agent: Agent, status: AgentStatus) -> Agent:
    was_active = agent.status == AgentStatus.ACTIVE
    agent.status = status
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    now_active = agent.status == AgentStatus.ACTIVE
    if was_active != now_active:
        await compliance_audit_agent.evaluate_event(
            db, tenant_id, "feature_flag.kill_switch_toggled",
            {
                "flag_id": str(agent.id), "target_agent_key": agent.key, "new_status": agent.status.value,
                "kill_switch_armed": not now_active, "agent_action_applied": True,
            },
        )
        await db.refresh(agent)
    return agent


def _known_agents() -> dict[str, dict]:
    """Every agent key the supervisor can dispatch to, with the events
    that trigger it and the roster agent it belongs to."""
    from src.services.supervisor import EVENT_ROUTING

    known: dict[str, dict] = {}
    for event_type, routes in EVENT_ROUTING.items():
        for agent_key, _handler in routes:
            known.setdefault(agent_key, {"triggers": []})["triggers"].append(event_type)
    for entry in ROSTER:
        for agent_key in entry.get("agent_keys", []):
            known.setdefault(agent_key, {"triggers": []}).setdefault("roster", []).append(entry["name"])
    return known


def _display_name(agent_key: str) -> str:
    return agent_key.removesuffix("_agent").replace("_", " ").title() + " Agent"


@router.get("/agents/registry")
async def agent_registry(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Every agent the platform runs, whether or not it has a registry row
    yet (no row means active), with its status, triggers and decisions."""
    rows = {a.key: a for a in (await db.execute(select(Agent).where(Agent.tenant_id == tenant_id))).scalars()}
    counts = dict((await db.execute(
        select(DecisionLog.agent_key, func.count()).where(DecisionLog.tenant_id == tenant_id).group_by(DecisionLog.agent_key)
    )).all())
    out = []
    for key, info in sorted(_known_agents().items()):
        row = rows.get(key)
        out.append({
            "key": key, "display_name": row.display_name if row else _display_name(key),
            "roster": sorted(set(info.get("roster", []))), "triggers": sorted(info["triggers"]),
            "status": (row.status if row else AgentStatus.ACTIVE).value, "registered": row is not None,
            "decisions": counts.get(key, 0),
        })
    return out


@router.put("/agents/registry/{agent_key}/status", response_model=AgentOut)
async def set_registry_status(
    agent_key: str, body: AgentStatusUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Pause/resume/disable any agent by key, registering it on first use."""
    if agent_key not in _known_agents():
        raise HTTPException(status_code=404, detail=f"No agent '{agent_key}' on this platform")
    agent = (await db.execute(select(Agent).where(Agent.key == agent_key, Agent.tenant_id == tenant_id))).scalar_one_or_none()
    if agent is None:
        agent = Agent(tenant_id=tenant_id, key=agent_key, display_name=_display_name(agent_key), status=AgentStatus.ACTIVE)
    return await _apply_status(db, tenant_id, agent, body.status)


@router.get("/roster")
async def get_roster():
    """The 12-agent roster (docs/AI_ORCHESTRATION_SCALING.md) and which
    code implements each — including what is still missing."""
    return ROSTER

