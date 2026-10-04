"""
Corporate & Campus Accounts engine.

Fully autonomous: creating a corporate account, adding/removing an
employee seat, and evaluating utilization all execute in one call with
no approval gate — none of this is an access-control decision, it's
account administration. The one hard business rule enforced
structurally rather than left to the caller: adding an employee beyond
seat_limit is refused outright, not silently allowed and reconciled
later.

Utilization monitoring is the AI-native piece: evaluate_utilization
scans every employee under a corporate account and autonomously writes
a flag — underutilizing (a seat-reallocation candidate) or
overutilizing (an upgrade candidate) — the same self-contained
decision-and-write-in-one-call pattern used everywhere else on this
platform. Resolving a flag (reallocating the seat, approving the
upgrade) is left to the corporate admin, since that's a real business
and cost decision about the company's own account, not something the
platform should do unilaterally on the company's behalf.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    CorporateAccount,
    CorporateAccountStatus,
    EmployeeUtilizationFlag,
    MemberProfile,
    MemberType,
    UtilizationFlagType,
)

UNDERUTILIZATION_TRIP_THRESHOLD = 2  # trips/30d at or below this flags for reallocation
OVERUTILIZATION_TRIP_THRESHOLD = 20  # trips/30d at or above this flags for an upgrade candidate


class SeatLimitExceededError(ValueError):
    pass


async def create_corporate_account(
    db: AsyncSession, tenant_id: str, company_name: str, billing_contact_email: str,
    seat_limit: int = 10, billing_type: str = "centralized",
) -> CorporateAccount:
    account = CorporateAccount(
        tenant_id=tenant_id, company_name=company_name, billing_contact_email=billing_contact_email,
        seat_limit=seat_limit, billing_type=billing_type,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


async def get_corporate_account(db: AsyncSession, tenant_id: str, organization_id: uuid.UUID) -> CorporateAccount | None:
    result = await db.execute(select(CorporateAccount).where(CorporateAccount.id == organization_id, CorporateAccount.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_corporate_accounts(db: AsyncSession, tenant_id: str) -> list[CorporateAccount]:
    result = await db.execute(select(CorporateAccount).where(CorporateAccount.tenant_id == tenant_id))
    return list(result.scalars().all())


async def list_employees(db: AsyncSession, tenant_id: str, organization_id: uuid.UUID) -> list[MemberProfile]:
    result = await db.execute(select(MemberProfile).where(MemberProfile.tenant_id == tenant_id, MemberProfile.organization_id == organization_id))
    return list(result.scalars().all())


async def add_employee(
    db: AsyncSession, tenant_id: str, organization_id: uuid.UUID, user_id: str, full_name: str, email: str,
) -> MemberProfile:
    """Refuses outright once the account is at its seat_limit — this
    is a hard business rule enforced here, not a soft warning the
    caller can proceed past."""
    account = await get_corporate_account(db, tenant_id, organization_id)
    if account is None:
        raise ValueError(f"corporate account {organization_id} not found")

    current_employees = await list_employees(db, tenant_id, organization_id)
    if len(current_employees) >= account.seat_limit:
        raise SeatLimitExceededError(f"corporate account '{account.company_name}' is at its seat limit ({account.seat_limit}) — remove a seat or raise the limit before adding another employee")

    employee = MemberProfile(
        tenant_id=tenant_id, user_id=user_id, full_name=full_name, email=email,
        member_type=MemberType.CORPORATE, organization_id=organization_id,
    )
    db.add(employee)
    await db.commit()
    await db.refresh(employee)
    return employee


async def remove_employee(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID) -> MemberProfile | None:
    """Frees the seat by clearing the organization link rather than
    deleting the member profile — the person's subscription history,
    GU ledger, and lifetime value don't disappear because their
    employer's seat was reassigned."""
    result = await db.execute(select(MemberProfile).where(MemberProfile.id == member_profile_id, MemberProfile.tenant_id == tenant_id))
    employee = result.scalar_one_or_none()
    if employee is None:
        return None
    employee.organization_id = None
    employee.member_type = MemberType.PERSONAL
    await db.commit()
    await db.refresh(employee)
    return employee


async def get_billing_summary(db: AsyncSession, tenant_id: str, organization_id: uuid.UUID) -> dict:
    """Real transaction arithmetic — no AI reasoning belongs on a
    billing summary, same principle as every other financial rollup
    on this platform."""
    account = await get_corporate_account(db, tenant_id, organization_id)
    employees = await list_employees(db, tenant_id, organization_id)
    total_ltv = sum(e.lifetime_value_usd for e in employees)
    return {
        "organization_id": str(organization_id),
        "company_name": account.company_name if account else None,
        "billing_type": account.billing_type.value if account else None,
        "seat_limit": account.seat_limit if account else 0,
        "seats_used": len(employees),
        "seats_available": (account.seat_limit - len(employees)) if account else 0,
        "total_lifetime_value_usd": round(total_ltv, 2),
        "total_gu_balance": sum(e.gu_balance for e in employees),
    }


def _score_utilization(trips_last_30d: int) -> tuple[UtilizationFlagType | None, str]:
    if trips_last_30d <= UNDERUTILIZATION_TRIP_THRESHOLD:
        return UtilizationFlagType.UNDERUTILIZING, f"[deterministic] {trips_last_30d} trip(s) in the last 30 days, at or below the {UNDERUTILIZATION_TRIP_THRESHOLD}-trip reallocation threshold."
    if trips_last_30d >= OVERUTILIZATION_TRIP_THRESHOLD:
        return UtilizationFlagType.OVERUTILIZING, f"[deterministic] {trips_last_30d} trip(s) in the last 30 days, at or above the {OVERUTILIZATION_TRIP_THRESHOLD}-trip upgrade-candidate threshold."
    return None, ""


async def evaluate_utilization(db: AsyncSession, tenant_id: str, organization_id: uuid.UUID, trips_by_member: dict[str, int]) -> list[EmployeeUtilizationFlag]:
    """Fully autonomous: scans every employee under the account and
    writes a flag for anyone outside the normal utilization band, in
    the same call — the corporate admin doesn't have to notice an
    underused or overused seat on their own. trips_by_member is
    supplied by the caller (a real trip-count query against
    fleet/rental data in production) rather than computed here, since
    trip history doesn't live in this service."""
    employees = await list_employees(db, tenant_id, organization_id)
    flags = []
    for employee in employees:
        trips = trips_by_member.get(str(employee.id), 0)
        flag_type, reasoning = _score_utilization(trips)
        if flag_type is None:
            continue
        flag = EmployeeUtilizationFlag(
            tenant_id=tenant_id, organization_id=organization_id, member_profile_id=employee.id,
            flag_type=flag_type, trips_last_30d=trips, reasoning=reasoning,
        )
        db.add(flag)
        flags.append(flag)
    if flags:
        await db.commit()
        for flag in flags:
            await db.refresh(flag)
    return flags


async def list_utilization_flags(db: AsyncSession, tenant_id: str, organization_id: uuid.UUID, unresolved_only: bool = True) -> list[EmployeeUtilizationFlag]:
    query = select(EmployeeUtilizationFlag).where(EmployeeUtilizationFlag.tenant_id == tenant_id, EmployeeUtilizationFlag.organization_id == organization_id)
    if unresolved_only:
        query = query.where(EmployeeUtilizationFlag.resolved == False)
    result = await db.execute(query)
    return list(result.scalars().all())


async def resolve_utilization_flag(db: AsyncSession, tenant_id: str, flag_id: uuid.UUID, resolved_by: str) -> EmployeeUtilizationFlag | None:
    """Resolving a flag (reallocating the seat, approving the
    upgrade) is the corporate admin's own decision about their
    company's account and cost structure — this records that
    decision, it doesn't make one."""
    result = await db.execute(select(EmployeeUtilizationFlag).where(EmployeeUtilizationFlag.id == flag_id, EmployeeUtilizationFlag.tenant_id == tenant_id))
    flag = result.scalar_one_or_none()
    if flag is None:
        return None
    flag.resolved = True
    flag.resolved_by = resolved_by
    await db.commit()
    await db.refresh(flag)
    return flag


async def suspend_corporate_account(db: AsyncSession, tenant_id: str, organization_id: uuid.UUID) -> CorporateAccount | None:
    account = await get_corporate_account(db, tenant_id, organization_id)
    if account is None:
        return None
    account.status = CorporateAccountStatus.SUSPENDED
    await db.commit()
    await db.refresh(account)
    return account


async def reactivate_corporate_account(db: AsyncSession, tenant_id: str, organization_id: uuid.UUID) -> CorporateAccount | None:
    account = await get_corporate_account(db, tenant_id, organization_id)
    if account is None:
        return None
    account.status = CorporateAccountStatus.ACTIVE
    await db.commit()
    await db.refresh(account)
    return account
