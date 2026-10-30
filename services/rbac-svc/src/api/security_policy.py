import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import account_status, audit_engine, security_policy

router = APIRouter(prefix="/api/v1/rbac/security-policy", tags=["settings"])
internal = APIRouter(prefix="/internal/security", tags=["internal"])

# Shown on the settings page as always on: fixed controls, not toggles.
ENFORCED = [
    {"key": "jwt_ttl", "label": "Access tokens expire after 15 minutes", "where": "auth-svc"},
    {"key": "httponly", "label": "Session tokens only in HttpOnly cookies (never localStorage)", "where": "auth-svc / console"},
    {"key": "lockout", "label": "Account locks for 15 minutes after 5 failed sign-ins", "where": "auth-svc"},
    {"key": "deny_wins", "label": "RBAC deny-wins: a DENY override beats every ALLOW", "where": "rbac-svc"},
    {"key": "audit", "label": "Every admin and AI decision written to the audit log", "where": "rbac-svc / ai-orchestration-svc"},
    {"key": "secrets", "label": "Integration credentials encrypted at rest", "where": "rbac-svc"},
    {"key": "api_keys", "label": "API-key authentication: not offered — every request needs a signed-in session", "where": "api-gateway-svc"},
]


class PolicyIn(BaseModel):
    require_mfa_for_operators: bool | None = None
    cors_allowed_origins: list[str] | None = None
    updated_by: str = Field(min_length=1)
    # The person saving; turning on required MFA is refused while their own
    # account has none, so they can't lock themselves out.
    actor_user_id: uuid.UUID | None = None


@router.get("")
async def get_policy(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return {**await security_policy.get(db, tenant_id), "enforced": ENFORCED}


@router.put("")
async def put_policy(body: PolicyIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    changes = body.model_dump(exclude_none=True, exclude={"updated_by", "actor_user_id"})
    current = await security_policy.get(db, tenant_id)
    if changes.get("require_mfa_for_operators") and not current["require_mfa_for_operators"]:
        if body.actor_user_id is None:
            raise HTTPException(status_code=422, detail="actor_user_id is required to turn on required MFA.")
        try:
            mine = (await account_status.fetch(tenant_id, [str(body.actor_user_id)])).get(str(body.actor_user_id))
        except account_status.AccountServiceError as exc:
            raise HTTPException(status_code=503, detail=f"Can't confirm your own MFA status: {exc}") from exc
        if mine is not None and not mine["mfa_enabled"]:
            raise HTTPException(status_code=409, detail="Set up MFA on your own account first (My profile) — otherwise this would lock you out.")
    try:
        policy, diff = await security_policy.save(db, tenant_id, changes, body.updated_by)
    except security_policy.PolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if diff:
        detail = "; ".join(f"{k}: {o} → {n}" for k, (o, n) in diff.items())
        await audit_engine.record(db, tenant_id, "human", body.updated_by, "security_policy.updated", "security.policy", detail)
    return {**policy, "enforced": ENFORCED}


def _require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    expected = get_settings().internal_token
    if not expected or not x_internal_token or not hmac.compare_digest(expected, x_internal_token):
        raise HTTPException(status_code=403, detail="internal endpoint")


@internal.get("/cors-origins", dependencies=[Depends(_require_internal)])
async def cors_origins(db: AsyncSession = Depends(get_db)):
    """Every origin any tenant allows — the gateway answers CORS before it knows the tenant."""
    return {"origins": await security_policy.all_cors_origins(db)}
