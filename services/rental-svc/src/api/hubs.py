from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import demand_engine, reallocation_engine, utilization_engine

router = APIRouter(prefix="/api/v1/rental/hubs", tags=["hubs"])


@router.get("/{hub_id}/utilization")
async def get_utilization(hub_id: str, rental_class: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The exact endpoint rental_tools.get_utilization calls —
    utilization_pct is the supply-side signal the Pricing Intelligence
    Agent reads before recommending a rate."""
    return await utilization_engine.compute_utilization(db, tenant_id, hub_id, rental_class)


@router.get("/{hub_id}/demand-forecast")
async def get_demand_forecast(hub_id: str, rental_class: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The demand-side signal utilization alone cannot provide —
    booking velocity and trend, generated fresh on every call."""
    forecast = await demand_engine.generate_forecast(db, tenant_id, hub_id, rental_class)
    return {
        "hub_id": hub_id, "rental_class": rental_class, "booking_velocity_per_day": forecast.booking_velocity_per_day,
        "trend": forecast.trend.value, "forecasted_utilization_pct": forecast.forecasted_utilization_pct, "reasoning": forecast.reasoning,
    }


@router.get("/reallocation-opportunities")
async def find_reallocation_opportunities(rental_class: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Autonomous cross-hub scan — every pair of hubs carrying this
    class is compared, surfacing specific, actionable suggestions
    rather than a generic 'rebalance fleet' alert."""
    suggestions = await reallocation_engine.find_reallocation_opportunities(db, tenant_id, rental_class)
    return [
        {
            "rental_class": s.rental_class, "donor_hub_id": s.donor_hub_id, "donor_utilization_pct": s.donor_utilization_pct,
            "recipient_hub_id": s.recipient_hub_id, "recipient_utilization_pct": s.recipient_utilization_pct,
            "utilization_gap_pct": s.utilization_gap_pct, "reasoning": s.reasoning,
        }
        for s in suggestions
    ]
