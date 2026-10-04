import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import KycStatus, MemberType, MobilityClass, ServiceLevel
from src.services import member_engine, plan_engine

router = APIRouter(prefix="/api/v1/subscription", tags=["members-plans"])


class MemberCreate(BaseModel):
    user_id: str
    full_name: str
    email: str


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: str
    full_name: str
    email: str
    member_type: MemberType
    organization_id: uuid.UUID | None
    kyc_status: KycStatus
    mobility_class: MobilityClass | None
    service_level: ServiceLevel | None
    gu_balance: int
    lifetime_value_usd: float
    churn_risk_score: float | None
    upgrade_readiness_score: float | None
    is_voyqi_black: bool


class KycUpdate(BaseModel):
    status: KycStatus


class ServiceLevelUpdate(BaseModel):
    service_level: ServiceLevel


class ContactInfoUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None


class PlanCreate(BaseModel):
    mobility_class: MobilityClass
    market: str
    monthly_price_usd: float
    entitlements: dict = {}


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mobility_class: MobilityClass
    market: str
    monthly_price_usd: float
    entitlements: dict
    is_invitation_only: bool


class EquivalencyCreate(BaseModel):
    source_class: MobilityClass
    destination_market: str
    equivalent_class: MobilityClass
    notes: str = ""


@router.post("/members", response_model=MemberOut, status_code=201)
async def create_member(body: MemberCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await member_engine.create_member_profile(db, tenant_id=tenant_id, user_id=body.user_id, full_name=body.full_name, email=body.email)


@router.get("/members", response_model=list[MemberOut])
async def search_members(
    mobility_class: MobilityClass | None = None, kyc_status: KycStatus | None = None, is_voyqi_black: bool | None = None,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    return await member_engine.search_members(db, tenant_id, mobility_class=mobility_class, kyc_status=kyc_status, is_voyqi_black=is_voyqi_black)


@router.get("/members/{profile_id}", response_model=MemberOut)
async def get_member(profile_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    profile = await member_engine.get_member_profile(db, tenant_id, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return profile


@router.get("/members/by-user-id/{user_id}", response_model=MemberOut)
async def get_member_by_user_id(user_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The lookup a sign-in flow actually needs — resolving the
    internal profile id from the external user_id a client already
    has, rather than requiring the profile id up front."""
    profile = await member_engine.get_member_by_user_id(db, tenant_id, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return profile


@router.patch("/members/{profile_id}", response_model=MemberOut)
async def update_contact_info(profile_id: uuid.UUID, body: ContactInfoUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous — editing your own name/email is not an
    access-control decision."""
    profile = await member_engine.update_contact_info(db, tenant_id, profile_id, body.full_name, body.email)
    if profile is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return profile


@router.post("/members/{profile_id}/kyc", response_model=MemberOut)
async def update_kyc(profile_id: uuid.UUID, body: KycUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    profile = await member_engine.update_kyc_status(db, tenant_id, profile_id, body.status)
    if profile is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return profile


@router.post("/members/{profile_id}/service-level", response_model=MemberOut)
async def set_service_level(profile_id: uuid.UUID, body: ServiceLevelUpdate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    profile = await member_engine.set_service_level(db, tenant_id, profile_id, body.service_level)
    if profile is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return profile


@router.post("/plans", response_model=PlanOut, status_code=201)
async def create_plan(body: PlanCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await plan_engine.create_plan(
        db, tenant_id=tenant_id, mobility_class=body.mobility_class, market=body.market,
        monthly_price_usd=body.monthly_price_usd, entitlements=body.entitlements,
    )


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(market: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await plan_engine.list_plans(db, tenant_id, market=market)


@router.post("/class-equivalencies", status_code=201)
async def create_equivalency(body: EquivalencyCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    equivalency = await plan_engine.create_class_equivalency(
        db, tenant_id=tenant_id, source_class=body.source_class, destination_market=body.destination_market,
        equivalent_class=body.equivalent_class, notes=body.notes,
    )
    return {"id": str(equivalency.id)}


@router.get("/class-equivalencies/resolve")
async def resolve_equivalency(
    source_class: MobilityClass, destination_market: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    resolved = await plan_engine.resolve_equivalent_class(db, tenant_id, source_class, destination_market)
    return {"resolved_class": resolved.value}
