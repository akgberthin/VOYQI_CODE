import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import lending_engine

router = APIRouter(prefix="/api/v1/dms/lending", tags=["lending"])


class LenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str


class LenderCreate(BaseModel):
    name: str


@router.post("/lenders", response_model=LenderOut, status_code=201)
async def create_lender(body: LenderCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await lending_engine.create_lender(db, tenant_id, body.name)


@router.get("/lenders", response_model=list[LenderOut])
async def list_lenders(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await lending_engine.list_lenders(db, tenant_id)


class CreditApplicationCreate(BaseModel):
    deal_id: uuid.UUID
    applicant_name: str
    annual_income_usd: float


class CreditApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    deal_id: uuid.UUID
    applicant_name: str
    annual_income_usd: float
    status: str
    bureau: str | None
    fico: int | None
    ofac_match: bool
    selected_submission_id: uuid.UUID | None


@router.post("/credit-applications", response_model=CreditApplicationOut, status_code=201)
async def create_credit_application(body: CreditApplicationCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await lending_engine.create_credit_application(db, tenant_id, body.deal_id, body.applicant_name, body.annual_income_usd)


@router.get("/credit-applications", response_model=list[CreditApplicationOut])
async def list_credit_applications(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await lending_engine.list_credit_applications(db, tenant_id)


@router.get("/credit-applications/{app_id}", response_model=CreditApplicationOut)
async def get_credit_application(app_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    app = await lending_engine.get_credit_application(db, tenant_id, app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Credit application not found")
    return app


class BureauPullRequest(BaseModel):
    bureau: str
    fico: int
    ofac_match: bool = False


@router.post("/credit-applications/{app_id}/pull-bureau", response_model=CreditApplicationOut)
async def pull_bureau(app_id: uuid.UUID, body: BureauPullRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """OFAC is a structural block, checked here before any routing
    logic runs at all: an ofac_match=True never reaches
    auto_route_to_lenders — there is no override path, anywhere in
    this call graph. A clean pull chains straight into fully
    autonomous routing, decisioning, and winner selection."""
    try:
        return await lending_engine.pull_bureau(db, tenant_id, app_id, body.bureau, body.fico, body.ofac_match)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class SubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    credit_application_id: uuid.UUID
    lender_id: uuid.UUID
    status: str
    apr: float | None
    term_months: int | None
    amount_usd: float | None
    reserve_usd: float | None


@router.get("/credit-applications/{app_id}/submissions", response_model=list[SubmissionOut])
async def get_submissions(app_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await lending_engine.get_submissions(db, tenant_id, app_id)
