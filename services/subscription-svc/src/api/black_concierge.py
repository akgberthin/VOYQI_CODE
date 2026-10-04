import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import BlackInvitationStatus, ConciergeJourneyStatus, ConciergeMode
from src.services import black_concierge_engine

router = APIRouter(prefix="/api/v1/subscription", tags=["black-concierge"])


class InviteRequest(BaseModel):
    member_profile_id: uuid.UUID
    invited_by: str


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_profile_id: uuid.UUID
    status: BlackInvitationStatus
    invited_by: str


class RespondRequest(BaseModel):
    accept: bool


class EntitlementCreate(BaseModel):
    key: str
    name: str
    description: str = ""


class JourneyStart(BaseModel):
    member_profile_id: uuid.UUID
    journey_type: str
    mode: ConciergeMode
    assigned_concierge: str = ""


class JourneyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_profile_id: uuid.UUID
    journey_type: str
    mode: ConciergeMode
    status: ConciergeJourneyStatus
    assigned_concierge: str
    satisfaction_score: int | None
    created_at: datetime


class JourneyComplete(BaseModel):
    satisfaction_score: int | None = None


@router.post("/black-invitations", response_model=InvitationOut, status_code=201)
async def invite_to_black(body: InviteRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await black_concierge_engine.invite_to_black(db, tenant_id, body.member_profile_id, body.invited_by)


@router.post("/black-invitations/{invitation_id}/respond", response_model=InvitationOut)
async def respond_to_invitation(invitation_id: uuid.UUID, body: RespondRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    invitation = await black_concierge_engine.respond_to_invitation(db, tenant_id, invitation_id, body.accept)
    if invitation is None:
        raise HTTPException(status_code=409, detail="Invitation not found or already responded to")
    return invitation


@router.post("/black-entitlements", status_code=201)
async def create_entitlement(body: EntitlementCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    entitlement = await black_concierge_engine.create_black_entitlement(db, tenant_id, body.key, body.name, body.description)
    return {"id": str(entitlement.id), "key": entitlement.key}


@router.get("/black-members")
async def list_black_members(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    members = await black_concierge_engine.list_black_members(db, tenant_id)
    return [{"id": str(m.id), "full_name": m.full_name} for m in members]


@router.post("/concierge-journeys", response_model=JourneyOut, status_code=201)
async def start_journey(body: JourneyStart, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await black_concierge_engine.start_journey(
        db, tenant_id, body.member_profile_id, body.journey_type, body.mode, body.assigned_concierge
    )


@router.post("/concierge-journeys/{journey_id}/complete", response_model=JourneyOut)
async def complete_journey(journey_id: uuid.UUID, body: JourneyComplete, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    journey = await black_concierge_engine.complete_journey(db, tenant_id, journey_id, body.satisfaction_score)
    if journey is None:
        raise HTTPException(status_code=409, detail="Journey not found or already completed")
    return journey


@router.get("/concierge-journeys/active", response_model=list[JourneyOut])
async def list_active_journeys(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await black_concierge_engine.list_active_journeys(db, tenant_id)
