import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import ApplicationStatus
from src.services import application_engine

router = APIRouter(prefix="/api/v1/originations/applications", tags=["applications"])


class ApplicationIn(BaseModel):
    applicant_name: str
    requested_amount_usd: float
    annual_income_usd: float
    fico_score: int


class ApplicationOut(BaseModel):
    id: str
    applicant_name: str
    requested_amount_usd: float
    annual_income_usd: float
    fico_score: int
    status: ApplicationStatus


def _to_out(a) -> ApplicationOut:
    return ApplicationOut(
        id=str(a.id), applicant_name=a.applicant_name, requested_amount_usd=a.requested_amount_usd,
        annual_income_usd=a.annual_income_usd, fico_score=a.fico_score, status=a.status,
    )


@router.post("", response_model=ApplicationOut, status_code=201)
async def create_application(
    payload: ApplicationIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> ApplicationOut:
    application = await application_engine.create_application(
        db, tenant_id, payload.applicant_name, payload.requested_amount_usd, payload.annual_income_usd, payload.fico_score
    )
    return _to_out(application)


@router.get("", response_model=list[ApplicationOut])
async def list_applications(
    status: ApplicationStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> list[ApplicationOut]:
    applications = await application_engine.list_applications(db, tenant_id, status)
    return [_to_out(a) for a in applications]


@router.get("/{application_id}", response_model=ApplicationOut)
async def get_application(
    application_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> ApplicationOut:
    application = await application_engine.get_application(db, tenant_id, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return _to_out(application)
