"""
MOB-003 Enrollment & Onboarding Queue engine.

Approval hands off to Sales & Originations for eligibility/financing
checks (not built yet) — enroll() enforces that an application must be
APPROVED before it can become a member profile + subscription, so that
handoff can be inserted later without weakening this gate.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    Application,
    ApplicationStatus,
    MemberProfile,
    MobilityClass,
    VoyqiCard,
)


class ApplicationNotApprovedError(ValueError):
    pass


async def submit_application(
    db: AsyncSession, tenant_id: str, applicant_name: str, applicant_email: str,
    requested_class: MobilityClass, market: str,
) -> Application:
    application = Application(
        tenant_id=tenant_id, applicant_name=applicant_name, applicant_email=applicant_email,
        requested_class=requested_class, market=market,
    )
    db.add(application)
    await db.commit()
    await db.refresh(application)
    return application


async def review_application(
    db: AsyncSession, tenant_id: str, application_id: uuid.UUID, approve: bool, reviewed_by: str, rejection_reason: str = ""
) -> Application | None:
    result = await db.execute(select(Application).where(Application.id == application_id, Application.tenant_id == tenant_id))
    application = result.scalar_one_or_none()
    if application is None:
        return None
    application.status = ApplicationStatus.APPROVED if approve else ApplicationStatus.REJECTED
    application.reviewed_by = reviewed_by
    if not approve:
        application.rejection_reason = rejection_reason
    await db.commit()
    await db.refresh(application)
    return application


async def enroll(db: AsyncSession, tenant_id: str, application_id: uuid.UUID, user_id: str) -> MemberProfile:
    result = await db.execute(select(Application).where(Application.id == application_id, Application.tenant_id == tenant_id))
    application = result.scalar_one_or_none()
    if application is None:
        raise ValueError(f"application {application_id} not found")
    if application.status != ApplicationStatus.APPROVED:
        raise ApplicationNotApprovedError(
            f"application {application_id} is '{application.status.value}', not approved — cannot enroll"
        )

    profile = MemberProfile(
        tenant_id=tenant_id, user_id=user_id, full_name=application.applicant_name,
        email=application.applicant_email, mobility_class=application.requested_class,
    )
    db.add(profile)
    await db.flush()
    application.status = ApplicationStatus.ENROLLED
    application.member_profile_id = profile.id
    await db.commit()
    await db.refresh(profile)
    return profile


async def issue_card(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, card_id: str) -> VoyqiCard:
    card = VoyqiCard(tenant_id=tenant_id, member_profile_id=member_profile_id, card_id=card_id)
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return card


async def bind_card(db: AsyncSession, tenant_id: str, card_id_pk: uuid.UUID) -> VoyqiCard | None:
    result = await db.execute(select(VoyqiCard).where(VoyqiCard.id == card_id_pk, VoyqiCard.tenant_id == tenant_id))
    card = result.scalar_one_or_none()
    if card is None:
        return None
    card.is_bound = True
    await db.commit()
    await db.refresh(card)
    return card
