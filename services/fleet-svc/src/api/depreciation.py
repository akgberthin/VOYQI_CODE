import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import depreciation_engine

router = APIRouter(prefix="/api/v1/fleet", tags=["depreciation"])


class ScheduleCreate(BaseModel):
    vehicle_id: uuid.UUID
    acquisition_cost_usd: float
    salvage_value_usd: float = 0.0
    useful_life_months: int


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    acquisition_cost_usd: float
    salvage_value_usd: float
    useful_life_months: int


class ValuationCreate(BaseModel):
    vehicle_id: uuid.UUID
    market_value_estimate_usd: float
    valued_by: str


@router.post("/depreciation-schedules", response_model=ScheduleOut, status_code=201)
async def create_schedule(body: ScheduleCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await depreciation_engine.create_schedule(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, acquisition_cost_usd=body.acquisition_cost_usd,
        salvage_value_usd=body.salvage_value_usd, useful_life_months=body.useful_life_months,
    )


@router.get("/vehicles/{vehicle_id}/book-value")
async def get_book_value(vehicle_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await depreciation_engine.get_current_book_value(db, tenant_id, vehicle_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No depreciation schedule found for this vehicle")
    return result


@router.post("/asset-valuations", status_code=201)
async def record_market_valuation(
    body: ValuationCreate, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    valuation = await depreciation_engine.record_market_valuation(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, market_value_estimate_usd=body.market_value_estimate_usd,
        valued_by=body.valued_by,
    )
    # AI-first: every market valuation gets compared against book value
    # automatically. The depreciation schedule and this valuation
    # record are both untouched by this — it's a signal for finance
    # review only.
    book_value_info = await depreciation_engine.get_current_book_value(db, tenant_id, body.vehicle_id)
    if book_value_info is not None:
        background_tasks.add_task(
            publish_event,
            "fleet.depreciation",
            "flt.valuation_anomaly_requested",
            tenant_id,
            {"vehicle_id": str(body.vehicle_id), "asset_valuation_id": str(valuation.id), "book_value_usd": book_value_info["book_value_usd"], "market_value_usd": valuation.market_value_estimate_usd},
        )
    return {"id": str(valuation.id), "market_value_estimate_usd": valuation.market_value_estimate_usd}


@router.post("/asset-valuations/{asset_valuation_id}/assess-anomaly-now")
async def assess_valuation_anomaly_now(asset_valuation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation market
    valuation recording already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    valuation = await depreciation_engine.get_asset_valuation(db, tenant_id, asset_valuation_id)
    if valuation is None:
        raise HTTPException(status_code=404, detail="Asset valuation not found")
    book_value_info = await depreciation_engine.get_current_book_value(db, tenant_id, valuation.vehicle_id)
    if book_value_info is None:
        raise HTTPException(status_code=409, detail="No depreciation schedule found for this vehicle")
    response = ai_orchestration_tools.request_valuation_anomaly(
        tenant_id, str(valuation.vehicle_id), str(asset_valuation_id), book_value_info["book_value_usd"], valuation.market_value_estimate_usd,
    )
    if response is None:
        raise HTTPException(status_code=502, detail="Fleet Intelligence Agent unreachable")
    return response


class ValuationAnomalyCreate(BaseModel):
    vehicle_id: uuid.UUID
    deviation_pct: float
    assessment: str
    reasoning: str = ""


@router.post("/asset-valuations/{asset_valuation_id}/anomaly-assessments")
async def create_valuation_anomaly(asset_valuation_id: uuid.UUID, body: ValuationAnomalyCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db), background_tasks: BackgroundTasks = None):
    """The agent's FLT-006 tool call. A FLAGGED assessment chains
    autonomously into FLT-013: the agent doesn't just note the
    anomaly, it immediately requests a full fleet-optimization review
    for the vehicle, which (per FLT-013's own autonomous chain) can go
    on to create a real disposition order — no human has to notice the
    flag and manually trigger the next step."""
    from src.models import ValuationAnomalyLevel
    from src.services import analytics_engine

    assessment_level = ValuationAnomalyLevel(body.assessment)
    record = await depreciation_engine.record_valuation_anomaly(
        db, tenant_id, body.vehicle_id, asset_valuation_id, body.deviation_pct, assessment_level, body.reasoning,
    )
    chained = False
    if assessment_level == ValuationAnomalyLevel.FLAGGED and background_tasks is not None:
        signals = await analytics_engine.gather_fleet_optimization_signals(db, tenant_id, body.vehicle_id)
        background_tasks.add_task(
            publish_event, "fleet.depreciation", "flt.fleet_optimization_requested", tenant_id, signals,
        )
        chained = True
    return {"id": str(record.id), "assessment": record.assessment.value, "deviation_pct": record.deviation_pct, "chained_to_fleet_optimization": chained}


@router.get("/asset-valuations/{asset_valuation_id}/anomaly-assessment")
async def get_valuation_anomaly(asset_valuation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    record = await depreciation_engine.get_valuation_anomaly(db, tenant_id, asset_valuation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No anomaly assessment on file")
    return {"id": str(record.id), "assessment": record.assessment.value, "deviation_pct": record.deviation_pct, "reasoning": record.reasoning}
