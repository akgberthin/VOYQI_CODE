"""
SAP-003 — ABAC policy authoring engine.

simulate is Tier 1: it re-runs automatically on every edit, has no
approval gate, and previewing a proposed grant carries none of the
risk actually granting it does. apply_policy is the hard Tier-3 gate:
it structurally requires a non-empty accountable_role and cannot be
reached by any risk score — there is no threshold, no "auto-apply if
conflicts_detected is empty" shortcut, anywhere in this function.
Broad access-control grants are the one place on this platform where
that shortcut is never the right call.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ABACPolicy, ABACPolicyStatus, Role


async def create_policy(db: AsyncSession, tenant_id: str, name: str, source_role_key: str, target_bundle_key: str) -> ABACPolicy:
    policy = ABACPolicy(tenant_id=tenant_id, name=name, source_role_key=source_role_key, target_bundle_key=target_bundle_key)
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


async def get_policy(db: AsyncSession, tenant_id: str, policy_id: uuid.UUID) -> ABACPolicy | None:
    result = await db.execute(select(ABACPolicy).where(ABACPolicy.id == policy_id, ABACPolicy.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_policies(db: AsyncSession, tenant_id: str) -> list[ABACPolicy]:
    result = await db.execute(select(ABACPolicy).where(ABACPolicy.tenant_id == tenant_id))
    return list(result.scalars().all())


async def detect_conflicts(db: AsyncSession, tenant_id: str, target_bundle_key: str) -> list[str]:
    """Deterministic conflict check against existing roles' permission
    sets — a real (if simple) rule engine, not a placeholder."""
    result = await db.execute(select(Role).where(Role.tenant_id == tenant_id))
    roles = list(result.scalars().all())
    target = next((r for r in roles if r.key == target_bundle_key), None)
    if target is None:
        return [f"target bundle '{target_bundle_key}' does not exist as a defined role"]

    conflicts = []
    for role in roles:
        if role.key == target_bundle_key:
            continue
        overlap = set(role.permission_keys) & set(target.permission_keys)
        if overlap and role.is_system_role:
            conflicts.append(f"overlaps {len(overlap)} permission(s) already exclusive to system role '{role.key}'")
    return conflicts


async def simulate(db: AsyncSession, tenant_id: str, policy_id: uuid.UUID) -> ABACPolicy:
    """Fully autonomous: re-simulates on every call, no approval
    needed to preview. This never changes what any user can actually
    do — it only updates conflicts_detected on the policy record."""
    policy = await get_policy(db, tenant_id, policy_id)
    if policy is None:
        raise ValueError(f"policy {policy_id} not found")
    policy.conflicts_detected = await detect_conflicts(db, tenant_id, policy.target_bundle_key)
    policy.simulated_at = datetime.now(timezone.utc)
    if policy.status == ABACPolicyStatus.DRAFT:
        policy.status = ABACPolicyStatus.SIMULATED
    await db.commit()
    await db.refresh(policy)
    return policy


class AccountableRoleRequiredError(ValueError):
    pass


class PolicyNotSimulatedError(ValueError):
    pass


async def apply_policy(db: AsyncSession, tenant_id: str, policy_id: uuid.UUID, accountable_role: str, applied_by: str) -> ABACPolicy:
    """The hard Tier-3 gate. Structurally refuses to proceed without a
    real, non-empty accountable_role, and refuses to apply a policy
    that has never been simulated — there is no risk score, no
    threshold, and no bypass anywhere in this function. This mirrors
    the Red Flags Rule override gate in dms-svc exactly: a missing
    required human input raises before anything else executes."""
    if not accountable_role or not accountable_role.strip():
        raise AccountableRoleRequiredError("an accountable role must be named before a policy can be applied")

    policy = await get_policy(db, tenant_id, policy_id)
    if policy is None:
        raise ValueError(f"policy {policy_id} not found")
    if policy.simulated_at is None:
        raise PolicyNotSimulatedError("a policy must be simulated at least once before it can be applied")

    policy.status = ABACPolicyStatus.APPLIED
    policy.accountable_role = accountable_role.strip()
    policy.applied_by = applied_by
    policy.applied_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(policy)
    return policy


async def reject_policy(db: AsyncSession, tenant_id: str, policy_id: uuid.UUID) -> ABACPolicy:
    policy = await get_policy(db, tenant_id, policy_id)
    if policy is None:
        raise ValueError(f"policy {policy_id} not found")
    policy.status = ABACPolicyStatus.REJECTED
    await db.commit()
    await db.refresh(policy)
    return policy
