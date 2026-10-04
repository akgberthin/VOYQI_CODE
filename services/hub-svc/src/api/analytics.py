import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import ai_orchestration_tools, analytics_engine

router = APIRouter(prefix="/api/v1/hub", tags=["analytics"])


@router.get("/hubs/{hub_id}/throughput")
async def get_throughput(hub_id: uuid.UUID, window_days: int = 7, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await analytics_engine.get_throughput(db, tenant_id, hub_id, window_days=window_days)


@router.get("/hubs/{hub_id}/dwell-time")
async def get_dwell_time(hub_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await analytics_engine.get_dwell_time_hours(db, tenant_id, hub_id)


@router.get("/hubs/{hub_id}/utilization")
async def get_utilization(hub_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await analytics_engine.get_capacity_utilization(db, tenant_id, hub_id)


@router.get("/network-snapshot")
async def get_network_snapshot(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await analytics_engine.get_network_snapshot(db, tenant_id)


class RebalancingRequest(BaseModel):
    reason: str = "network-wide rebalancing check"


@router.post("/hubs/{hub_id}/rebalancing-recommendation")
async def get_rebalancing_recommendation(hub_id: uuid.UUID, body: RebalancingRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    response = ai_orchestration_tools.request_hub_rebalancing(tenant_id, str(hub_id), body.reason)
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response
