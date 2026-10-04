import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.kafka_events import publish_event
from src.models import CompliancePolicy, GrantRequestStatus, PolicyGrantRequest, PolicyStatus, SubjectType


class PolicyNotFoundError(Exception):
    pass


class NotSimulatedError(Exception):
    """Raised when apply is attempted before simulate — the hard gate
    has no path around this, not even for an admin in a hurry."""


class AccountableRoleRequiredError(Exception):
    """Raised when apply is attempted without a named accountable
    role — the second half of the hard gate."""


class AlreadyDecidedError(Exception):
    pass


async def request_grant(
    db: AsyncSession, tenant_id: str, policy_id: uuid.UUID, subject_type: SubjectType, subject_id: str
) -> PolicyGrantRequest:
    result = await db.execute(
        select(CompliancePolicy).where(CompliancePolicy.tenant_id == tenant_id, CompliancePolicy.id == policy_id)
    )
    if result.scalar_one_or_none() is None:
        raise PolicyNotFoundError(f"policy {policy_id} not found")

    request = PolicyGrantRequest(tenant_id=tenant_id, policy_id=policy_id, subject_type=subject_type, subject_id=subject_id)
    db.add(request)
    await db.commit()
    await db.refresh(request)
    return request


async def get_grant_request(db: AsyncSession, tenant_id: str, request_id: uuid.UUID) -> PolicyGrantRequest | None:
    result = await db.execute(
        select(PolicyGrantRequest).where(PolicyGrantRequest.tenant_id == tenant_id, PolicyGrantRequest.id == request_id)
    )
    return result.scalar_one_or_none()


async def list_grant_requests(
    db: AsyncSession, tenant_id: str, status: GrantRequestStatus | None = None
) -> list[PolicyGrantRequest]:
    stmt = select(PolicyGrantRequest).where(PolicyGrantRequest.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(PolicyGrantRequest.status == status)
    result = await db.execute(stmt.order_by(PolicyGrantRequest.created_at.desc()))
    return list(result.scalars().all())


async def simulate_grant(db: AsyncSession, tenant_id: str, request_id: uuid.UUID) -> PolicyGrantRequest | None:
    """A dry run — records what applying this grant would mean, so
    approval is of a known effect, not a guess. Deterministic: checks
    the target policy is active and whether the same subject already
    has a pending or approved request against it."""
    request = await get_grant_request(db, tenant_id, request_id)
    if request is None:
        return None

    policy_result = await db.execute(
        select(CompliancePolicy).where(CompliancePolicy.id == request.policy_id)
    )
    policy = policy_result.scalar_one()

    conflicts_result = await db.execute(
        select(PolicyGrantRequest).where(
            PolicyGrantRequest.tenant_id == tenant_id,
            PolicyGrantRequest.policy_id == request.policy_id,
            PolicyGrantRequest.subject_id == request.subject_id,
            PolicyGrantRequest.id != request.id,
            PolicyGrantRequest.status.in_([GrantRequestStatus.PENDING, GrantRequestStatus.APPROVED]),
        )
    )
    conflicts = [str(r.id) for r in conflicts_result.scalars().all()]

    policy_active = policy.status == PolicyStatus.ACTIVE
    simulation_result = {
        "policy_active": policy_active,
        "conflicting_request_ids": conflicts,
        "safe_to_apply": policy_active and not conflicts,
    }

    request.simulated = True
    request.simulation_result = simulation_result
    await db.commit()
    await db.refresh(request)
    return request


async def apply_grant(
    db: AsyncSession, tenant_id: str, request_id: uuid.UUID, actor: str, accountable_role: str | None
) -> PolicyGrantRequest | None:
    """The hard gate. No risk score, no threshold, no AI code path —
    this function is the only place a grant can move to APPROVED, and
    it refuses to do so without both a prior simulation and a named
    accountable role, full stop."""
    request = await get_grant_request(db, tenant_id, request_id)
    if request is None:
        return None
    if request.status != GrantRequestStatus.PENDING:
        raise AlreadyDecidedError(f"grant request already {request.status.value}")
    if not request.simulated:
        raise NotSimulatedError("a simulation must be run before this grant can be applied")
    if not accountable_role:
        raise AccountableRoleRequiredError("accountable_role is required to apply a policy grant")

    request.status = GrantRequestStatus.APPROVED
    request.accountable_role = accountable_role
    request.decided_by = actor
    request.decided_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(request)

    await publish_event(
        "security_compliance.policy_grant_applied",
        {
            "tenant_id": tenant_id, "request_id": str(request.id), "policy_id": str(request.policy_id),
            "subject_type": request.subject_type.value, "subject_id": request.subject_id,
            "accountable_role": accountable_role,
        },
    )
    return request


async def reject_grant(db: AsyncSession, tenant_id: str, request_id: uuid.UUID, actor: str) -> PolicyGrantRequest | None:
    request = await get_grant_request(db, tenant_id, request_id)
    if request is None:
        return None
    if request.status != GrantRequestStatus.PENDING:
        raise AlreadyDecidedError(f"grant request already {request.status.value}")

    request.status = GrantRequestStatus.REJECTED
    request.decided_by = actor
    request.decided_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(request)
    return request
