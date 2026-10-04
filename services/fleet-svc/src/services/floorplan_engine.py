"""
FLT-008/009 Fleet Financing & Floorplan engine.

is_covenant_breached and every dollar figure on FloorplanFacility/
VinCollateralStatus remain exact deterministic arithmetic — lenders
require precise, auditable collateral certainty that can never be
AI-judged. What the Fleet Intelligence Agent DOES act on autonomously:
the instant evaluate_covenant_risk classifies a facility as BREACHED,
auto_freeze_facility is called immediately, blocking all further
pledges until a human unfreezes it. This is a real operational lock,
not just a logged warning.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    CovenantRiskAssessment,
    CovenantRiskLevel,
    FloorplanFacility,
    VinCollateralStatus,
)


class InsufficientCapacityError(ValueError):
    pass


class FacilityFrozenError(ValueError):
    pass


async def create_facility(
    db: AsyncSession, tenant_id: str, lender: str, credit_limit_usd: float, max_utilization_pct: float = 90.0
) -> FloorplanFacility:
    facility = FloorplanFacility(
        tenant_id=tenant_id, lender=lender, credit_limit_usd=credit_limit_usd, max_utilization_pct=max_utilization_pct,
    )
    db.add(facility)
    await db.commit()
    await db.refresh(facility)
    return facility


def utilization_pct(facility: FloorplanFacility) -> float:
    if facility.credit_limit_usd == 0:
        return 0.0
    return (facility.utilized_amount_usd / facility.credit_limit_usd) * 100


def is_covenant_breached(facility: FloorplanFacility) -> bool:
    return utilization_pct(facility) > facility.max_utilization_pct


async def pledge_vehicle(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, facility_id: uuid.UUID, pledged_amount_usd: float
) -> tuple[VinCollateralStatus, bool]:
    """Returns (collateral_status, covenant_breached). Pledging is
    blocked outright if the facility is frozen (a prior autonomous
    breach response) or if it would exceed the facility's hard credit
    limit; max_utilization_pct is a softer covenant that CAN be crossed
    (the loan didn't bounce) — it just gets flagged for review rather
    than blocked at the arithmetic layer (the agent's autonomous freeze
    is what actually blocks further pledges once breached)."""
    result = await db.execute(select(FloorplanFacility).where(FloorplanFacility.id == facility_id, FloorplanFacility.tenant_id == tenant_id))
    facility = result.scalar_one_or_none()
    if facility is None:
        raise ValueError(f"facility {facility_id} not found")
    if facility.frozen:
        raise FacilityFrozenError(
            f"facility {facility.lender} is frozen ({facility.frozen_reason}) — no further pledges until a human unfreezes it"
        )
    if facility.utilized_amount_usd + pledged_amount_usd > facility.credit_limit_usd:
        raise InsufficientCapacityError(
            f"pledging ${pledged_amount_usd:,.2f} would exceed facility credit limit "
            f"(${facility.utilized_amount_usd:,.2f} already utilized of ${facility.credit_limit_usd:,.2f})"
        )

    collateral = VinCollateralStatus(
        tenant_id=tenant_id, vehicle_id=vehicle_id, facility_id=facility_id, pledged_amount_usd=pledged_amount_usd,
    )
    db.add(collateral)
    facility.utilized_amount_usd += pledged_amount_usd
    breached = is_covenant_breached(facility)

    await db.commit()
    await db.refresh(collateral)
    return collateral, breached


async def auto_freeze_facility(db: AsyncSession, tenant_id: str, facility_id: uuid.UUID, reason: str, agent_key: str) -> FloorplanFacility | None:
    """Fully autonomous FLT-008/009 execution: called the instant a
    covenant breach is detected, this immediately blocks all further
    pledges against the facility. No human confirmation gate — a
    human unfreezes once the facility is brought back into compliance."""
    result = await db.execute(select(FloorplanFacility).where(FloorplanFacility.id == facility_id, FloorplanFacility.tenant_id == tenant_id))
    facility = result.scalar_one_or_none()
    if facility is None:
        return None
    facility.frozen = True
    facility.frozen_reason = f"[auto-frozen by {agent_key}] {reason}"
    await db.commit()
    await db.refresh(facility)
    return facility


async def unfreeze_facility(db: AsyncSession, tenant_id: str, facility_id: uuid.UUID) -> FloorplanFacility | None:
    """Human-only action — the one deliberate exception to full
    automation in this module, because clearing a lender covenant
    breach is an attestation a human needs to make."""
    result = await db.execute(select(FloorplanFacility).where(FloorplanFacility.id == facility_id, FloorplanFacility.tenant_id == tenant_id))
    facility = result.scalar_one_or_none()
    if facility is None:
        return None
    facility.frozen = False
    facility.frozen_reason = ""
    await db.commit()
    await db.refresh(facility)
    return facility


async def release_vehicle(db: AsyncSession, tenant_id: str, collateral_id: uuid.UUID) -> VinCollateralStatus | None:
    result = await db.execute(select(VinCollateralStatus).where(VinCollateralStatus.id == collateral_id, VinCollateralStatus.tenant_id == tenant_id))
    collateral = result.scalar_one_or_none()
    if collateral is None or collateral.status == "released":
        return None
    collateral.status = "released"

    facility_result = await db.execute(select(FloorplanFacility).where(FloorplanFacility.id == collateral.facility_id))
    facility = facility_result.scalar_one_or_none()
    if facility:
        facility.utilized_amount_usd = max(0.0, facility.utilized_amount_usd - collateral.pledged_amount_usd)

    await db.commit()
    await db.refresh(collateral)
    return collateral


async def get_facility(db: AsyncSession, tenant_id: str, facility_id: uuid.UUID) -> FloorplanFacility | None:
    result = await db.execute(select(FloorplanFacility).where(FloorplanFacility.id == facility_id, FloorplanFacility.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def record_covenant_risk(db: AsyncSession, tenant_id: str, facility_id: uuid.UUID, risk_level: CovenantRiskLevel, reasoning: str) -> CovenantRiskAssessment:
    assessment = CovenantRiskAssessment(tenant_id=tenant_id, facility_id=facility_id, risk_level=risk_level, reasoning=reasoning)
    db.add(assessment)
    await db.commit()
    await db.refresh(assessment)
    return assessment


async def get_latest_covenant_risk(db: AsyncSession, tenant_id: str, facility_id: uuid.UUID) -> CovenantRiskAssessment | None:
    result = await db.execute(select(CovenantRiskAssessment).where(CovenantRiskAssessment.tenant_id == tenant_id, CovenantRiskAssessment.facility_id == facility_id).order_by(CovenantRiskAssessment.computed_at.desc()))
    return result.scalars().first()
