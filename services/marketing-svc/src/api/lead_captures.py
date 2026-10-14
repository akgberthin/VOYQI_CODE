"""Landing-page lead capture.

Submitting is public (an anonymous visitor fills the form), so it has
its own /public/ path the gateway lets through without a JWT; listing
captures stays authenticated. The gateway matches public paths by path
only, which is why the two are not the same path with different methods.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import Campaign, LeadCapture

router = APIRouter(prefix="/api/v1/marketing", tags=["lead-captures"])


class LeadCaptureIn(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    landing_page: str = ""
    campaign_id: uuid.UUID | None = None
    consent_marketing: bool = False

    @model_validator(mode="after")
    def _needs_contact(self):
        if not self.email and not (self.phone or "").strip():
            raise ValueError("email or phone is required")
        return self


class LeadCaptureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    campaign_id: uuid.UUID | None
    landing_page: str
    name: str
    email: str | None
    phone: str | None
    consent_marketing: bool
    created_at: datetime | None


@router.post("/public/lead-captures", response_model=LeadCaptureOut, status_code=201)
async def capture_lead(body: LeadCaptureIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    if body.campaign_id is not None:
        result = await db.execute(select(Campaign).where(Campaign.id == body.campaign_id, Campaign.tenant_id == tenant_id))
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
    capture = LeadCapture(
        tenant_id=tenant_id, campaign_id=body.campaign_id, landing_page=body.landing_page, name=body.name.strip(),
        email=body.email, phone=(body.phone or "").strip() or None, consent_marketing=body.consent_marketing,
    )
    db.add(capture)
    await db.commit()
    await db.refresh(capture)
    background_tasks.add_task(
        publish_event, "marketing.leads", "lead.captured", tenant_id,
        {"lead_id": str(capture.id), "name": capture.name, "email": capture.email or "", "phone": capture.phone or "", "landing_page": capture.landing_page},
    )
    return capture


@router.get("/lead-captures", response_model=list[LeadCaptureOut])
async def list_lead_captures(campaign_id: uuid.UUID | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    query = select(LeadCapture).where(LeadCapture.tenant_id == tenant_id)
    if campaign_id is not None:
        query = query.where(LeadCapture.campaign_id == campaign_id)
    result = await db.execute(query.order_by(LeadCapture.created_at.desc()))
    return list(result.scalars().all())
