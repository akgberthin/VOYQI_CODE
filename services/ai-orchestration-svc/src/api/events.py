from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services.supervisor import route_event

router = APIRouter(prefix="/api/v1/ai", tags=["events"])


class EventIn(BaseModel):
    event_type: str
    payload: dict


@router.post("/events")
async def ingest_event(
    body: EventIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    """The same routing path the Kafka consumer calls per message — exposed
    directly so events can be replayed or driven synchronously without a
    live broker (used by every execution service's background-task
    publisher in dev, and by ops for incident replay in production)."""
    results = await route_event(db, tenant_id, body.event_type, body.payload)
    return {"routed_to": len(results), "event_type": body.event_type}
