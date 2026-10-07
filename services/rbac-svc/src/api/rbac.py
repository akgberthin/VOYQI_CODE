import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import audit_engine, rbac_engine

router = APIRouter(prefix="/api/v1/rbac", tags=["rbac"])


class UserCreate(BaseModel):
    email: str
    display_name: str
    mfa_enabled: bool = False


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    display_name: str
    mfa_enabled: bool
    status: str


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await rbac_engine.create_platform_user(db, tenant_id, body.email, body.display_name, body.mfa_enabled)


@router.get("/users", response_model=list[UserOut])
async def list_users(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await rbac_engine.list_platform_users(db, tenant_id)


class RoleCreate(BaseModel):
    key: str
    name: str
    permission_keys: list[str] = []
    is_system_role: bool = False
    wildcard_permissions: list[str] = []


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    key: str
    name: str
    is_system_role: bool
    permission_keys: list
    wildcard_permissions: list


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(body: RoleCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await rbac_engine.create_role(db, tenant_id, body.key, body.name, body.permission_keys, body.is_system_role, body.wildcard_permissions)


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await rbac_engine.list_roles(db, tenant_id)


class RoleAssignmentCreate(BaseModel):
    user_id: uuid.UUID
    role_id: uuid.UUID
    assigned_by: str


class RoleAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID
    role_id: uuid.UUID
    assigned_by: str


@router.post("/role-assignments", response_model=RoleAssignmentOut, status_code=201)
async def create_role_assignment(body: RoleAssignmentCreate, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous-eligible — assigning an already-defined role
    is a routine grant, not new access-control policy authorship."""
    assignment = await rbac_engine.create_role_assignment(db, tenant_id, body.user_id, body.role_id, body.assigned_by)
    await audit_engine.record(db, tenant_id, "human" if not body.assigned_by.startswith("ai:") else "ai", body.assigned_by, "role_assignment.created", str(body.user_id), f"role_id={body.role_id}")
    background_tasks.add_task(
        publish_event, "rbac.access", "role.assigned", tenant_id,
        {"assignment_id": str(assignment.id), "user_id": str(body.user_id), "role_id": str(body.role_id), "assigned_by": body.assigned_by, "ai_initiated": body.assigned_by.startswith("ai:")},
    )
    return assignment


class PermissionOverrideCreate(BaseModel):
    user_id: uuid.UUID
    permission_key: str
    effect: str
    granted_by: str
    reason: str = ""


class PermissionOverrideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID
    permission_key: str
    effect: str
    granted_by: str
    reason: str
    risk_flagged: bool
    revoked: bool
    revoked_by: str | None
    revoked_reason: str | None


@router.post("/permission-overrides", response_model=PermissionOverrideOut, status_code=201)
async def create_permission_override(body: PermissionOverrideCreate, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    override = await rbac_engine.create_permission_override(db, tenant_id, body.user_id, body.permission_key, body.effect, body.granted_by, body.reason)
    await audit_engine.record(db, tenant_id, "human", body.granted_by, "permission_override.granted", str(body.user_id), f"permission_key={body.permission_key} effect={body.effect}")
    background_tasks.add_task(
        publish_event, "rbac.access", "permission.granted", tenant_id,
        {
            "override_id": str(override.id), "user_id": str(body.user_id), "permission_key": body.permission_key,
            "effect": body.effect, "granted_by": body.granted_by, "reason": body.reason,
            "is_sensitive": rbac_engine.is_sensitive_permission(body.permission_key),
            "ai_initiated": body.granted_by.startswith(("ai:", "ai-orchestration")),
        },
    )
    return override


@router.get("/permission-overrides", response_model=list[PermissionOverrideOut])
async def list_all_permission_overrides(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Tenant-wide — what the Super Admin Panel's overview needs, not
    scoped to a single user like the agent-facing context endpoint."""
    return await rbac_engine.list_all_permission_overrides(db, tenant_id)


class AutoRevokeRequest(BaseModel):
    threshold: int = 50
    revoked_by: str = "compliance_audit_agent"


@router.post("/permission-overrides/{override_id}/auto-revoke-if-unwarranted", response_model=PermissionOverrideOut)
async def auto_revoke_if_unwarranted(override_id: uuid.UUID, body: AutoRevokeRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Called by the Compliance/Audit Agent below its risk threshold.
    At or above threshold, the agent queues an AIRecommendation for
    human review instead of ever calling this endpoint."""
    try:
        override, was_revoked = await rbac_engine.auto_revoke_if_unwarranted(db, tenant_id, override_id, body.threshold, body.revoked_by)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if was_revoked:
        await audit_engine.record(db, tenant_id, "ai", body.revoked_by, "permission_override.auto_revoked", str(override_id), override.revoked_reason or "")
    return override


class RevokeRequest(BaseModel):
    revoked_by: str
    reason: str


@router.post("/permission-overrides/{override_id}/revoke", response_model=PermissionOverrideOut)
async def revoke_permission_override(override_id: uuid.UUID, body: RevokeRequest, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        override = await rbac_engine.revoke_permission_override(db, tenant_id, override_id, body.revoked_by, body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    actor_type = "ai" if body.revoked_by.startswith("ai-orchestration-svc") else "human"
    await audit_engine.record(db, tenant_id, actor_type, body.revoked_by, "permission_override.revoked", str(override_id), body.reason)
    background_tasks.add_task(
        publish_event, "rbac.access", "permission.revoked", tenant_id,
        {"override_id": str(override_id), "user_id": str(override.user_id), "permission_key": override.permission_key, "revoked_by": body.revoked_by, "reason": body.reason},
    )
    return override


@router.get("/users/{user_id}/context")
async def get_user_rbac_context(user_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Exact contract rbac_tools.get_user_rbac_context expects."""
    return await rbac_engine.get_user_rbac_context(db, tenant_id, user_id)


@router.get("/users/{user_id}/effective-permissions")
async def resolve_effective_permissions(user_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await rbac_engine.resolve_effective_permissions(db, tenant_id, user_id)
