from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import pricing_intelligence_agent

router = APIRouter(prefix="/api/v1/ai/rental", tags=["pricing-intelligence-agent"])


class PricingEvaluationRequest(BaseModel):
    reservation_id: str
    hub_id: str
    rental_class: str


@router.post("/evaluate-pricing")
async def evaluate_pricing(body: PricingEvaluationRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await pricing_intelligence_agent.evaluate_rental_pricing(
        db, tenant_id=tenant_id, reservation_id=body.reservation_id, hub_id=body.hub_id, rental_class=body.rental_class
    )
    return {
        "new_rate": result.new_rate,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


dms_router = APIRouter(prefix="/api/v1/ai/dms", tags=["pricing-intelligence-agent"])


class TradeAppraisalRequest(BaseModel):
    appraisal_id: str
    vin: str
    mileage: int
    condition_notes: str = ""


@dms_router.post("/evaluate-trade-appraisal")
async def evaluate_trade_appraisal(body: TradeAppraisalRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await pricing_intelligence_agent.evaluate_trade_appraisal(
        db, tenant_id=tenant_id, appraisal_id=body.appraisal_id, vin=body.vin, mileage=body.mileage, condition_notes=body.condition_notes
    )
    return {
        "valuation_usd": result.valuation_usd,
        "offer_usd": result.offer_usd,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class RetailPricingRequest(BaseModel):
    retail_vehicle_id: str
    vin: str
    asking_price_usd: float
    days_in_inventory: int
    aging_threshold_days: int = 60


@dms_router.post("/evaluate-retail-pricing")
async def evaluate_retail_pricing(body: RetailPricingRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await pricing_intelligence_agent.evaluate_retail_pricing(
        db, tenant_id=tenant_id, retail_vehicle_id=body.retail_vehicle_id, vin=body.vin,
        asking_price_usd=body.asking_price_usd, days_in_inventory=body.days_in_inventory, aging_threshold_days=body.aging_threshold_days,
    )
    return {
        "recommended_price_usd": result.recommended_price_usd,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class MaxBidRequest(BaseModel):
    listing_id: str
    vin: str
    condition_grade: float = 3.0


@dms_router.post("/evaluate-max-bid")
async def evaluate_max_bid(body: MaxBidRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await pricing_intelligence_agent.evaluate_max_bid(
        db, tenant_id=tenant_id, listing_id=body.listing_id, vin=body.vin, condition_grade=body.condition_grade
    )
    return {
        "max_bid_usd": result.max_bid_usd,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }
