"""
Hub Capacity & Utilization Analytics — HUB-003.

Every number here is computed directly from touchpoint events and hub
capacity — the same principle as dms-svc's P&L board: no AI reasoning
belongs on an operational metrics surface where the numbers need to be
exactly reproducible. This data becomes the real anchor an AI agent
(the existing Fleet Intelligence Agent) can later read via a tool call
when reasoning about where to stage vehicles — but the computation
itself is deterministic.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Hub, TouchpointEvent, TouchpointStatus


async def get_throughput(db: AsyncSession, tenant_id: str, hub_id: uuid.UUID, window_days: int = 7) -> dict:
    """Completed touchpoints per day over the trailing window."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    result = await db.execute(
        select(TouchpointEvent).where(
            TouchpointEvent.tenant_id == tenant_id,
            TouchpointEvent.hub_id == hub_id,
            TouchpointEvent.status == TouchpointStatus.COMPLETED,
        )
    )
    events = [e for e in result.scalars().all() if _as_aware(e.completed_at) and _as_aware(e.completed_at) >= since]
    return {
        "hub_id": str(hub_id),
        "window_days": window_days,
        "completed_touchpoints": len(events),
        "touchpoints_per_day": round(len(events) / window_days, 2) if window_days else 0.0,
    }


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def get_dwell_time_hours(db: AsyncSession, tenant_id: str, hub_id: uuid.UUID) -> dict:
    """Average hours between a vehicle's scheduled_at and completed_at
    for completed touchpoints at this hub — how long staff actually
    take to turn a vehicle around."""
    result = await db.execute(
        select(TouchpointEvent).where(
            TouchpointEvent.tenant_id == tenant_id,
            TouchpointEvent.hub_id == hub_id,
            TouchpointEvent.status == TouchpointStatus.COMPLETED,
        )
    )
    events = list(result.scalars().all())
    if not events:
        return {"hub_id": str(hub_id), "avg_dwell_hours": None, "sample_size": 0}

    durations = []
    for e in events:
        scheduled = _as_aware(e.scheduled_at)
        completed = _as_aware(e.completed_at)
        if scheduled and completed:
            durations.append((completed - scheduled).total_seconds() / 3600.0)

    avg = round(sum(durations) / len(durations), 2) if durations else None
    return {"hub_id": str(hub_id), "avg_dwell_hours": avg, "sample_size": len(durations)}


async def get_capacity_utilization(db: AsyncSession, tenant_id: str, hub_id: uuid.UUID) -> dict:
    """Vehicles currently staged (scheduled or completed pickups net of
    returns, in the simplest deterministic reading: open — not yet
    completed-and-departed — touchpoints currently occupying a slot)
    versus the hub's configured capacity."""
    hub_result = await db.execute(select(Hub).where(Hub.id == hub_id, Hub.tenant_id == tenant_id))
    hub = hub_result.scalar_one_or_none()
    if hub is None:
        return {"hub_id": str(hub_id), "error": "hub not found"}

    result = await db.execute(
        select(TouchpointEvent).where(
            TouchpointEvent.tenant_id == tenant_id,
            TouchpointEvent.hub_id == hub_id,
            TouchpointEvent.status == TouchpointStatus.SCHEDULED,
        )
    )
    scheduled_count = len(list(result.scalars().all()))
    utilization_pct = round((scheduled_count / hub.capacity) * 100, 1) if hub.capacity else 0.0

    return {
        "hub_id": str(hub_id),
        "capacity": hub.capacity,
        "vehicles_scheduled": scheduled_count,
        "utilization_pct": utilization_pct,
        "at_capacity": scheduled_count >= hub.capacity,
    }


async def get_network_snapshot(db: AsyncSession, tenant_id: str) -> list[dict]:
    """One utilization row per active hub — the operator dashboard's
    network-wide view, and the real data the Fleet Intelligence Agent's
    hub-rebalancing capability reads before recommending anything."""
    result = await db.execute(select(Hub).where(Hub.tenant_id == tenant_id, Hub.is_active))
    hubs = list(result.scalars().all())
    snapshot = []
    for hub in hubs:
        util = await get_capacity_utilization(db, tenant_id, hub.id)
        snapshot.append({**util, "name": hub.name, "hub_type": hub.hub_type.value, "latitude": hub.latitude, "longitude": hub.longitude})
    return snapshot
