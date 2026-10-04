"""
MOB-009 VOYQI Black(TM) Administration + MOB-010 Concierge Operations
Console engines.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    BlackEntitlement,
    BlackInvitation,
    BlackInvitationStatus,
    ConciergeJourney,
    ConciergeJourneyStatus,
    ConciergeMode,
    MemberProfile,
)


async def invite_to_black(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, invited_by: str) -> BlackInvitation:
    invitation = BlackInvitation(tenant_id=tenant_id, member_profile_id=member_profile_id, invited_by=invited_by)
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)
    return invitation


async def respond_to_invitation(db: AsyncSession, tenant_id: str, invitation_id: uuid.UUID, accept: bool) -> BlackInvitation | None:
    result = await db.execute(select(BlackInvitation).where(BlackInvitation.id == invitation_id, BlackInvitation.tenant_id == tenant_id))
    invitation = result.scalar_one_or_none()
    if invitation is None or invitation.status != BlackInvitationStatus.INVITED:
        return None

    invitation.status = BlackInvitationStatus.ACCEPTED if accept else BlackInvitationStatus.DECLINED
    invitation.responded_at = datetime.now(timezone.utc)

    if accept:
        profile_result = await db.execute(select(MemberProfile).where(MemberProfile.id == invitation.member_profile_id))
        profile = profile_result.scalar_one_or_none()
        if profile:
            profile.is_voyqi_black = True

    await db.commit()
    await db.refresh(invitation)
    return invitation


async def create_black_entitlement(db: AsyncSession, tenant_id: str, key: str, name: str, description: str = "") -> BlackEntitlement:
    entitlement = BlackEntitlement(tenant_id=tenant_id, key=key, name=name, description=description)
    db.add(entitlement)
    await db.commit()
    await db.refresh(entitlement)
    return entitlement


async def list_black_members(db: AsyncSession, tenant_id: str) -> list[MemberProfile]:
    result = await db.execute(select(MemberProfile).where(MemberProfile.tenant_id == tenant_id, MemberProfile.is_voyqi_black.is_(True)))
    return list(result.scalars().all())


async def start_journey(
    db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, journey_type: str, mode: ConciergeMode, assigned_concierge: str = ""
) -> ConciergeJourney:
    journey = ConciergeJourney(
        tenant_id=tenant_id, member_profile_id=member_profile_id, journey_type=journey_type,
        mode=mode, assigned_concierge=assigned_concierge,
    )
    db.add(journey)
    await db.commit()
    await db.refresh(journey)
    return journey


async def complete_journey(db: AsyncSession, tenant_id: str, journey_id: uuid.UUID, satisfaction_score: int | None = None) -> ConciergeJourney | None:
    result = await db.execute(select(ConciergeJourney).where(ConciergeJourney.id == journey_id, ConciergeJourney.tenant_id == tenant_id))
    journey = result.scalar_one_or_none()
    if journey is None or journey.status != ConciergeJourneyStatus.ACTIVE:
        return None
    journey.status = ConciergeJourneyStatus.COMPLETED
    journey.satisfaction_score = satisfaction_score
    journey.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(journey)
    return journey


async def list_active_journeys(db: AsyncSession, tenant_id: str) -> list[ConciergeJourney]:
    result = await db.execute(
        select(ConciergeJourney).where(ConciergeJourney.tenant_id == tenant_id, ConciergeJourney.status == ConciergeJourneyStatus.ACTIVE)
    )
    return list(result.scalars().all())
