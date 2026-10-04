import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import appraisal_engine, compliance_engine

router = APIRouter(prefix="/api/v1/dms/appraisals", tags=["appraisals"])


class AppraisalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    vin: str
    mileage: int
    valuation_usd: float | None
    offer_usd: float | None
    status: str


class FindOrCreateRequest(BaseModel):
    vin: str
    mileage: int
    stolen_vehicle_match: bool = False


@router.post("/find-or-create", response_model=AppraisalOut)
async def find_or_create_appraisal(body: FindOrCreateRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The stolen-vehicle registry check happens here, at the API
    layer, BEFORE the appraisal is ever handed to the Pricing
    Intelligence Agent for valuation — this is a dms-svc security rule,
    not an AI judgment call, and it has no override. A match routes
    straight to a compliance hold and the agent is never even invoked."""
    appraisal = await appraisal_engine.find_or_create_appraisal(db, tenant_id, body.vin, body.mileage)
    if body.stolen_vehicle_match:
        appraisal = await appraisal_engine.set_security_hold(db, tenant_id, appraisal.id)
        await compliance_engine.create_hold(
            db, tenant_id, "stolen_vehicle", "Stolen-vehicle registry match — appraisal blocked",
            f"VIN {body.vin} matched an active theft report during trade-in appraisal. No override exists — "
            f"held for law-enforcement/security review.",
            80,
        )
    return appraisal


@router.get("", response_model=list[AppraisalOut])
async def list_appraisals(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await appraisal_engine.list_appraisals(db, tenant_id)


@router.get("/{appraisal_id}", response_model=AppraisalOut)
async def get_appraisal(appraisal_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    appraisal = await appraisal_engine.get_appraisal(db, tenant_id, appraisal_id)
    if appraisal is None:
        raise HTTPException(status_code=404, detail="Appraisal not found")
    return appraisal


class ValuationRequest(BaseModel):
    valuation_usd: float


@router.post("/{appraisal_id}/valuation-and-offer", response_model=AppraisalOut)
async def apply_valuation_and_offer(appraisal_id: uuid.UUID, body: ValuationRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Called by the Pricing Intelligence Agent after it computes a
    valuation — writes valuation AND offer in the same call, no
    separate acceptance step. Refuses if the appraisal is under
    security hold; there's no path around that from here."""
    try:
        return await appraisal_engine.apply_valuation_and_offer(db, tenant_id, appraisal_id, body.valuation_usd)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class AcceptRequest(BaseModel):
    rooftop_id: uuid.UUID


@router.post("/{appraisal_id}/accept", response_model=AppraisalOut)
async def accept_appraisal(appraisal_id: uuid.UUID, body: AcceptRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Accepting the offer is the customer's own decision — this
    records that real-world outcome, it doesn't compute one."""
    try:
        return await appraisal_engine.accept_appraisal(db, tenant_id, appraisal_id, body.rooftop_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{appraisal_id}/decline", response_model=AppraisalOut)
async def decline_appraisal(appraisal_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await appraisal_engine.decline_appraisal(db, tenant_id, appraisal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
