import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import ApplicationStatus, MobilityClass
from src.services import enrollment_engine
from src.services.enrollment_engine import ApplicationNotApprovedError

router = APIRouter(prefix="/api/v1/subscription", tags=["enrollment"])


class ApplicationCreate(BaseModel):
    applicant_name: str
    applicant_email: str
    requested_class: MobilityClass
    market: str


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    applicant_name: str
    applicant_email: str
    requested_class: MobilityClass
    status: ApplicationStatus
    reviewed_by: str | None
    member_profile_id: uuid.UUID | None
    created_at: datetime


class ReviewRequest(BaseModel):
    approve: bool
    reviewed_by: str
    rejection_reason: str = ""


class EnrollRequest(BaseModel):
    user_id: str


class CardIssueRequest(BaseModel):
    member_profile_id: uuid.UUID
    card_id: str


@router.post("/applications", response_model=ApplicationOut, status_code=201)
async def submit_application(body: ApplicationCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await enrollment_engine.submit_application(
        db, tenant_id=tenant_id, applicant_name=body.applicant_name, applicant_email=body.applicant_email,
        requested_class=body.requested_class, market=body.market,
    )


@router.post("/applications/{application_id}/review", response_model=ApplicationOut)
async def review_application(application_id: uuid.UUID, body: ReviewRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    application = await enrollment_engine.review_application(
        db, tenant_id, application_id, body.approve, body.reviewed_by, body.rejection_reason
    )
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


@router.post("/applications/{application_id}/enroll")
async def enroll(application_id: uuid.UUID, body: EnrollRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        profile = await enrollment_engine.enroll(db, tenant_id, application_id, body.user_id)
    except ApplicationNotApprovedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"member_profile_id": str(profile.id)}


@router.post("/voyqi-cards", status_code=201)
async def issue_card(body: CardIssueRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    card = await enrollment_engine.issue_card(db, tenant_id, body.member_profile_id, body.card_id)
    return {"id": str(card.id), "card_id": card.card_id}


@router.post("/voyqi-cards/{card_id}/bind")
async def bind_card(card_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    card = await enrollment_engine.bind_card(db, tenant_id, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return {"id": str(card.id), "is_bound": card.is_bound}
