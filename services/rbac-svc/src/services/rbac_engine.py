"""
SAP-002 — RBAC engine: roles, role assignments, and permission
overrides.

resolve_effective_permissions is where deny-wins is actually enforced
(not just documented): a DENY override always beats every ALLOW,
whether that ALLOW came from a role or another override. Role
assignment itself is fully autonomous-eligible — see create_role_assignment.
Permission-override risk monitoring is deliberately NOT full automation;
see auto_revoke_if_unwarranted, called by the Compliance/Audit Agent in
ai-orchestration-svc below its risk threshold, with anything at or
above queued for human review there instead.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    OverrideEffect,
    PermissionOverride,
    PlatformUser,
    Role,
    RoleAssignment,
)

SENSITIVE_PERMISSION_PREFIXES = ("rbac.", "abac.", "billing.refund", "compliance.")


def is_sensitive_permission(permission_key: str) -> bool:
    return permission_key.startswith(SENSITIVE_PERMISSION_PREFIXES)


async def create_role(db: AsyncSession, tenant_id: str, key: str, name: str, permission_keys: list[str], is_system_role: bool = False, wildcard_permissions: list[str] | None = None) -> Role:
    role = Role(tenant_id=tenant_id, key=key, name=name, permission_keys=permission_keys, is_system_role=is_system_role, wildcard_permissions=wildcard_permissions or [])
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return role


async def list_roles(db: AsyncSession, tenant_id: str) -> list[Role]:
    result = await db.execute(select(Role).where(Role.tenant_id == tenant_id))
    return list(result.scalars().all())


class DuplicatePlatformUserError(Exception):
    pass


async def create_platform_user(
    db: AsyncSession, tenant_id: str, email: str, display_name: str, mfa_enabled: bool = False, user_id: uuid.UUID | None = None,
) -> PlatformUser:
    if user_id is not None and await db.get(PlatformUser, user_id) is not None:
        raise DuplicatePlatformUserError(f"platform user {user_id} already exists")
    user = PlatformUser(tenant_id=tenant_id, email=email, display_name=display_name, mfa_enabled=mfa_enabled)
    if user_id is not None:
        user.id = user_id
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def list_platform_users(db: AsyncSession, tenant_id: str) -> list[PlatformUser]:
    result = await db.execute(select(PlatformUser).where(PlatformUser.tenant_id == tenant_id))
    return list(result.scalars().all())


async def create_role_assignment(db: AsyncSession, tenant_id: str, user_id: uuid.UUID, role_id: uuid.UUID, assigned_by: str) -> RoleAssignment:
    """Fully autonomous-eligible: assigning an already-defined,
    already-reviewed role to a user is a routine grant, not new
    access-control policy authorship. See ABACPolicy for the
    capability that actually changes what a role grants — that one
    has the hard Tier-3 gate; this one doesn't need it."""
    assignment = RoleAssignment(tenant_id=tenant_id, user_id=user_id, role_id=role_id, assigned_by=assigned_by)
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


async def revoke_role_assignment(db: AsyncSession, tenant_id: str, assignment_id: uuid.UUID) -> RoleAssignment:
    """Removes the assignment outright — the audit log and the published
    role.revoked event are the record that it existed."""
    result = await db.execute(select(RoleAssignment).where(RoleAssignment.id == assignment_id, RoleAssignment.tenant_id == tenant_id))
    assignment = result.scalar_one_or_none()
    if assignment is None:
        raise ValueError(f"role assignment {assignment_id} not found")
    await db.delete(assignment)
    await db.commit()
    return assignment


async def get_user_role_assignments(db: AsyncSession, tenant_id: str, user_id: uuid.UUID) -> list[RoleAssignment]:
    result = await db.execute(select(RoleAssignment).where(RoleAssignment.tenant_id == tenant_id, RoleAssignment.user_id == user_id))
    return list(result.scalars().all())


async def create_permission_override(db: AsyncSession, tenant_id: str, user_id: uuid.UUID, permission_key: str, effect: str, granted_by: str, reason: str) -> PermissionOverride:
    override = PermissionOverride(tenant_id=tenant_id, user_id=user_id, permission_key=permission_key, effect=effect, granted_by=granted_by, reason=reason)
    db.add(override)
    await db.commit()
    await db.refresh(override)
    return override


async def get_user_permission_overrides(db: AsyncSession, tenant_id: str, user_id: uuid.UUID, active_only: bool = True) -> list[PermissionOverride]:
    query = select(PermissionOverride).where(PermissionOverride.tenant_id == tenant_id, PermissionOverride.user_id == user_id)
    if active_only:
        query = query.where(PermissionOverride.revoked == False)  # noqa: E712
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_all_permission_overrides(db: AsyncSession, tenant_id: str) -> list[PermissionOverride]:
    """Tenant-wide view, not scoped to one user — what the Super Admin
    Panel's overview page actually needs, as distinct from
    get_user_permission_overrides above, which the Compliance/Audit
    Agent uses when it already knows which user it's investigating."""
    result = await db.execute(select(PermissionOverride).where(PermissionOverride.tenant_id == tenant_id))
    return list(result.scalars().all())


async def get_user_rbac_context(db: AsyncSession, tenant_id: str, user_id: uuid.UUID) -> dict:
    """The read-only context the Compliance/Audit Agent builds before
    deciding anything — roles held plus active overrides, each
    override tagged with whether it touches a sensitive permission."""
    assignments = await get_user_role_assignments(db, tenant_id, user_id)
    role_ids = [a.role_id for a in assignments]
    roles = []
    if role_ids:
        result = await db.execute(select(Role).where(Role.id.in_(role_ids)))
        roles = list(result.scalars().all())

    overrides = await get_user_permission_overrides(db, tenant_id, user_id, active_only=True)
    return {
        "user_id": str(user_id),
        "roles": [{"key": r.key, "name": r.name} for r in roles],
        # Assignment ids, so an admin screen can revoke a specific role.
        "assignments": [
            {"id": str(a.id), "role_id": str(a.role_id), "role_key": next((r.key for r in roles if r.id == a.role_id), None), "assigned_by": a.assigned_by}
            for a in assignments
        ],
        "active_overrides": [
            {
                "id": str(o.id), "permission_key": o.permission_key, "effect": o.effect.value,
                "granted_by": o.granted_by, "reason": o.reason,
                "is_sensitive": is_sensitive_permission(o.permission_key),
            }
            for o in overrides
        ],
    }


async def resolve_effective_permissions(db: AsyncSession, tenant_id: str, user_id: uuid.UUID) -> dict:
    """Deny-wins, enforced here: build the ALLOW set from roles, then
    apply overrides — any DENY override removes a permission
    regardless of how many ALLOWs support it; any ALLOW override adds
    one even without a role granting it. Order of override application
    doesn't matter because DENY is checked last, unconditionally."""
    assignments = await get_user_role_assignments(db, tenant_id, user_id)
    role_ids = [a.role_id for a in assignments]
    allowed: set[str] = set()
    if role_ids:
        result = await db.execute(select(Role).where(Role.id.in_(role_ids)))
        for role in result.scalars().all():
            allowed.update(role.permission_keys)
            allowed.update(role.wildcard_permissions)

    overrides = await get_user_permission_overrides(db, tenant_id, user_id, active_only=True)
    denied: set[str] = set()
    for override in overrides:
        if override.effect == OverrideEffect.ALLOW:
            allowed.add(override.permission_key)
        else:
            denied.add(override.permission_key)

    effective = allowed - denied  # deny always wins, regardless of source
    return {"user_id": str(user_id), "effective_permissions": sorted(effective), "denied": sorted(denied)}


RISK_AUTO_REVOKE_KEYWORDS = ("wildcard", "*", "super_admin", "rbac.grant", "abac.apply")


def score_override_risk(override: PermissionOverride) -> tuple[int, list[str]]:
    """Deterministic, explainable: a broad or self-escalating grant
    scores higher than a narrow one. This is the floor the Compliance/
    Audit Agent's live reasoning is checked against, never replaced by."""
    score = 0
    drivers = []
    if override.effect == OverrideEffect.DENY:
        return 0, ["deny overrides narrow access; never auto-revoked as 'unwarranted'"]
    if any(kw in override.permission_key.lower() for kw in RISK_AUTO_REVOKE_KEYWORDS):
        score += 60
        drivers.append(f"permission key '{override.permission_key}' matches a broad/escalating pattern")
    if not override.reason or len(override.reason.strip()) < 10:
        score += 25
        drivers.append("no meaningful justification on file")
    if override.granted_by == override.tenant_id:  # placeholder self-grant heuristic
        score += 15
        drivers.append("granted by the same identity it applies to")
    return min(100, score), drivers


async def auto_revoke_if_unwarranted(db: AsyncSession, tenant_id: str, override_id: uuid.UUID, threshold: int, revoked_by: str) -> tuple[PermissionOverride, bool]:
    """Called by the Compliance/Audit Agent below its risk threshold —
    at or above threshold, the agent queues an AIRecommendation for
    human review instead of calling this at all. Returns
    (override, was_revoked). A DENY-effect override is structurally
    exempt from this path entirely — there's no 'unwarranted' case for
    revoking a restriction, and a low risk score must never be read as
    permission to do so; the exemption is a hard type check, not a
    score threshold, precisely because a score of 0 would otherwise
    satisfy 'below threshold' and trigger a revoke it was never meant to."""
    result = await db.execute(select(PermissionOverride).where(PermissionOverride.id == override_id, PermissionOverride.tenant_id == tenant_id))
    override = result.scalar_one_or_none()
    if override is None:
        raise ValueError(f"permission override {override_id} not found")

    if override.effect == OverrideEffect.DENY:
        await db.refresh(override)
        return override, False

    risk_score, drivers = score_override_risk(override)
    override.risk_flagged = risk_score >= threshold

    if risk_score < threshold and not override.revoked:
        override.revoked = True
        override.revoked_by = revoked_by
        override.revoked_reason = f"[deterministic] Auto-revoked, risk score {risk_score} below threshold {threshold}: {'; '.join(drivers)}."
        await db.commit()
        await db.refresh(override)
        return override, True

    await db.commit()
    await db.refresh(override)
    return override, False


async def revoke_permission_override(db: AsyncSession, tenant_id: str, override_id: uuid.UUID, revoked_by: str, reason: str) -> PermissionOverride:
    """Direct revocation call, used by both the human-facing endpoint
    and the agent's explicit revoke tool once it has already decided
    (via auto_revoke_if_unwarranted's scoring, or a human's own
    judgment) that this override should go."""
    result = await db.execute(select(PermissionOverride).where(PermissionOverride.id == override_id, PermissionOverride.tenant_id == tenant_id))
    override = result.scalar_one_or_none()
    if override is None:
        raise ValueError(f"permission override {override_id} not found")
    override.revoked = True
    override.revoked_by = revoked_by
    override.revoked_reason = reason
    await db.commit()
    await db.refresh(override)
    return override
