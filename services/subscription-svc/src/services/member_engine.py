"""
MOB-001 Member Directory & 360° Profile engine.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import KycStatus, MemberProfile, MobilityClass, ServiceLevel


async def create_member_profile(
    db: AsyncSession, tenant_id: str, user_id: str, full_name: str, email: str
) -> MemberProfile:
    profile = MemberProfile(tenant_id=tenant_id, user_id=user_id, full_name=full_name, email=email)
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


async def get_member_profile(db: AsyncSession, tenant_id: str, profile_id: uuid.UUID) -> MemberProfile | None:
    result = await db.execute(select(MemberProfile).where(MemberProfile.id == profile_id, MemberProfile.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def get_member_by_user_id(db: AsyncSession, tenant_id: str, user_id: str) -> MemberProfile | None:
    """The lookup a sign-in flow actually needs — by the external
    user_id (issued at account creation), not the internal profile id
    a client wouldn't have yet on first sign-in."""
    result = await db.execute(select(MemberProfile).where(MemberProfile.user_id == user_id, MemberProfile.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def update_contact_info(db: AsyncSession, tenant_id: str, profile_id: uuid.UUID, full_name: str | None = None, email: str | None = None) -> MemberProfile | None:
    """Fully autonomous — editing your own name/email is not an
    access-control decision. Only touches the fields actually
    supplied, so a client can update just one field without
    resending the whole profile."""
    profile = await get_member_profile(db, tenant_id, profile_id)
    if profile is None:
        return None
    if full_name is not None:
        profile.full_name = full_name
    if email is not None:
        profile.email = email
    await db.commit()
    await db.refresh(profile)
    return profile


async def search_members(
    db: AsyncSession, tenant_id: str, mobility_class: MobilityClass | None = None,
    kyc_status: KycStatus | None = None, is_voyqi_black: bool | None = None,
) -> list[MemberProfile]:
    stmt = select(MemberProfile).where(MemberProfile.tenant_id == tenant_id)
    if mobility_class:
        stmt = stmt.where(MemberProfile.mobility_class == mobility_class)
    if kyc_status:
        stmt = stmt.where(MemberProfile.kyc_status == kyc_status)
    if is_voyqi_black is not None:
        stmt = stmt.where(MemberProfile.is_voyqi_black == is_voyqi_black)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_kyc_status(db: AsyncSession, tenant_id: str, profile_id: uuid.UUID, status: KycStatus) -> MemberProfile | None:
    profile = await get_member_profile(db, tenant_id, profile_id)
    if profile is None:
        return None
    profile.kyc_status = status
    await db.commit()
    await db.refresh(profile)
    return profile


async def set_service_level(db: AsyncSession, tenant_id: str, profile_id: uuid.UUID, service_level: ServiceLevel) -> MemberProfile | None:
    profile = await get_member_profile(db, tenant_id, profile_id)
    if profile is None:
        return None
    profile.service_level = service_level
    await db.commit()
    await db.refresh(profile)
    return profile
