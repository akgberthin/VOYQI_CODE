from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import dealer_intelligence_agent

router = APIRouter(prefix="/api/v1/ai/dms/dealer-intelligence", tags=["dealer-intelligence-agent"])


def _response(result):
    return {
        "output": result.output,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


@router.post("/evaluate-license-renewals")
async def evaluate_license_renewals(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return _response(await dealer_intelligence_agent.evaluate_license_renewals(db, tenant_id))


@router.post("/evaluate-aging-sweep")
async def evaluate_aging_sweep(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return _response(await dealer_intelligence_agent.evaluate_aging_sweep(db, tenant_id))


class DealDeskRequest(BaseModel):
    deal_id: str


@router.post("/evaluate-deal-desk")
async def evaluate_deal_desk(body: DealDeskRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return _response(await dealer_intelligence_agent.evaluate_deal_desk(db, tenant_id, body.deal_id))


class LendingRequest(BaseModel):
    app_id: str
    bureau: str
    fico: int
    ofac_match: bool = False


@router.post("/evaluate-lending")
async def evaluate_lending(body: LendingRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """OFAC is enforced inside dms-svc, not here — this endpoint has
    no code path around it regardless of what's passed in."""
    return _response(await dealer_intelligence_agent.evaluate_lending(db, tenant_id, body.app_id, body.bureau, body.fico, body.ofac_match))


class ServiceDispatchRequest(BaseModel):
    rooftop_id: str
    vin: str
    customer_name: str
    description: str
    warranty: bool = False


@router.post("/evaluate-service-dispatch")
async def evaluate_service_dispatch(body: ServiceDispatchRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return _response(await dealer_intelligence_agent.evaluate_service_dispatch(db, tenant_id, body.rooftop_id, body.vin, body.customer_name, body.description, body.warranty))


@router.post("/evaluate-parts-reorder")
async def evaluate_parts_reorder(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return _response(await dealer_intelligence_agent.evaluate_parts_reorder(db, tenant_id))


@router.post("/evaluate-loaner-overdue")
async def evaluate_loaner_overdue(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return _response(await dealer_intelligence_agent.evaluate_loaner_overdue(db, tenant_id))


@router.post("/evaluate-franchise-remediation")
async def evaluate_franchise_remediation(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return _response(await dealer_intelligence_agent.evaluate_franchise_remediation(db, tenant_id))


@router.post("/evaluate-dealer-performance")
async def evaluate_dealer_performance(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The P&L capstone — reads real transaction arithmetic and
    autonomously triggers an accelerated markdown sweep for any
    rooftop verdicted 'intervene'."""
    return _response(await dealer_intelligence_agent.evaluate_dealer_performance(db, tenant_id))
