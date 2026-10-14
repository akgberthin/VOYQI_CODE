import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import crm_engine

router = APIRouter()


class CaptureLeadIn(BaseModel):
    """A rep entering a lead gives first/last name; the Revenue Agent
    creating one from a landing-page capture (CRM-002) gives full_name
    and may have only a phone."""

    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    source: str = "unknown"
    external_ref_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _blank_email_is_none(cls, data):
        if isinstance(data, dict) and data.get("email") == "":
            data = {**data, "email": None}
        return data

    @model_validator(mode="after")
    def _split_full_name(self):
        if not self.first_name and self.full_name:
            first, _, rest = self.full_name.strip().partition(" ")
            self.first_name, self.last_name = first, self.last_name or rest
        if not self.first_name:
            raise ValueError("first_name or full_name is required")
        if not self.email and not self.phone:
            raise ValueError("email or phone is required")
        self.last_name = self.last_name or ""
        return self


class LeadScoreIn(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    routed_to_team: str


class QualifyLeadIn(BaseModel):
    qualified: bool
    reason: str | None = None
    assigned_to: uuid.UUID | None = None


class ConvertLeadIn(BaseModel):
    deal_name: str
    value_cents: int
    owner_id: uuid.UUID
    mobility_class: str | None = None
    expected_close_date: datetime | None = None


class CreateDealIn(BaseModel):
    name: str
    value_cents: int
    owner_id: uuid.UUID
    mobility_class: str | None = None
    expected_close_date: datetime | None = None


class AdvanceStageIn(BaseModel):
    new_stage: str


class CloseDealIn(BaseModel):
    won: bool
    reason: str | None = None


class ReopenDealIn(BaseModel):
    new_stage: str = "prospecting"


class LogActivityIn(BaseModel):
    activity_type: str
    subject: str
    created_by: uuid.UUID
    deal_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    notes: str | None = None


def _serialize_lead(lead) -> dict:
    return {
        "id": lead.id, "first_name": lead.first_name, "last_name": lead.last_name, "email": lead.email, "phone": lead.phone,
        "status": lead.status, "source": lead.source, "external_ref_id": lead.external_ref_id,
        "score": lead.score, "score_reasoning": lead.score_reasoning, "routed_to_team": lead.routed_to_team,
    }


def _serialize_deal(deal) -> dict:
    return {
        "id": deal.id, "name": deal.name, "stage": deal.stage, "value_cents": deal.value_cents,
        "mobility_class": deal.mobility_class, "owner_id": deal.owner_id, "closed_at": deal.closed_at, "close_reason": deal.close_reason,
    }


@router.post("/leads", status_code=201)
async def capture_lead(payload: CaptureLeadIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    # No lead.captured here: that event comes from marketing-svc's
    # landing-page capture, and the Revenue Agent handles it by calling
    # *this* endpoint — publishing it again here would loop.
    lead = await crm_engine.capture_lead(
        db, tenant_id=tenant_id, first_name=payload.first_name, last_name=payload.last_name, email=payload.email or "",
        phone=payload.phone, source=payload.source, external_ref_id=payload.external_ref_id,
    )
    await db.commit()
    return _serialize_lead(lead)


@router.get("/leads/duplicates/check")
async def check_duplicate_leads(email: str = "", phone: str = "", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    leads = await crm_engine.find_duplicate_leads(db, tenant_id=tenant_id, email=email, phone=phone)
    return [_serialize_lead(lead) for lead in leads]


@router.post("/leads/{lead_id}/score")
async def score_lead(lead_id: uuid.UUID, payload: LeadScoreIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The Revenue Agent's CRM-002 write-back (lead.score_and_route, tier 1)."""
    try:
        lead = await crm_engine.score_lead(db, tenant_id=tenant_id, lead_id=lead_id, **payload.model_dump())
    except crm_engine.LeadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await db.commit()
    return _serialize_lead(lead)


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        lead = await crm_engine.get_lead(db, tenant_id=tenant_id, lead_id=lead_id)
    except crm_engine.LeadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _serialize_lead(lead)


@router.post("/leads/{lead_id}/qualify")
async def qualify_lead(lead_id: uuid.UUID, payload: QualifyLeadIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        lead = await crm_engine.qualify_lead(db, tenant_id=tenant_id, lead_id=lead_id, **payload.model_dump())
    except crm_engine.LeadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except crm_engine.LeadAlreadyConvertedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return _serialize_lead(lead)


@router.post("/leads/{lead_id}/convert", status_code=201)
async def convert_lead(lead_id: uuid.UUID, payload: ConvertLeadIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        deal = await crm_engine.convert_lead_to_deal(db, tenant_id=tenant_id, lead_id=lead_id, **payload.model_dump())
    except crm_engine.LeadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except crm_engine.LeadAlreadyConvertedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return _serialize_deal(deal)


@router.post("/deals", status_code=201)
async def create_deal(payload: CreateDealIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    deal = await crm_engine.create_deal(db, tenant_id=tenant_id, **payload.model_dump())
    await db.commit()
    return _serialize_deal(deal)


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        deal = await crm_engine.get_deal(db, tenant_id=tenant_id, deal_id=deal_id)
    except crm_engine.DealNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _serialize_deal(deal)


@router.post("/deals/{deal_id}/advance")
async def advance_deal(deal_id: uuid.UUID, payload: AdvanceStageIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        deal = await crm_engine.advance_deal_stage(db, tenant_id=tenant_id, deal_id=deal_id, new_stage=payload.new_stage)
    except crm_engine.DealNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except crm_engine.DealClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except crm_engine.InvalidStageTransitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return _serialize_deal(deal)


@router.post("/deals/{deal_id}/close")
async def close_deal(deal_id: uuid.UUID, payload: CloseDealIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        deal = await crm_engine.close_deal(db, tenant_id=tenant_id, deal_id=deal_id, **payload.model_dump())
    except crm_engine.DealNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except crm_engine.DealClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return _serialize_deal(deal)


@router.post("/deals/{deal_id}/reopen")
async def reopen_deal(deal_id: uuid.UUID, payload: ReopenDealIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        deal = await crm_engine.reopen_deal(db, tenant_id=tenant_id, deal_id=deal_id, new_stage=payload.new_stage)
    except crm_engine.DealNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except crm_engine.InvalidStageTransitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return _serialize_deal(deal)


@router.post("/activities", status_code=201)
async def log_activity(payload: LogActivityIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        activity = await crm_engine.log_activity(db, tenant_id=tenant_id, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return {"id": activity.id, "activity_type": activity.activity_type, "subject": activity.subject}


@router.get("/pipeline/summary")
async def pipeline_summary(owner_id: uuid.UUID | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await crm_engine.get_pipeline_summary(db, tenant_id=tenant_id, owner_id=owner_id)
