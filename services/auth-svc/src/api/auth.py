import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import revocation
from src.core.config import get_settings
from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_current_user, get_tenant_id
from src.core.tokens import decode_access_token
from src.services import auth_engine

# Auth events are security signals. Several are emitted on paths that end
# in an HTTPException, where FastAPI never runs BackgroundTasks — so they
# are scheduled as their own tasks (references kept so they aren't
# garbage-collected mid-flight). Never include passwords or tokens.
_pending_events: set[asyncio.Task] = set()


def _emit(event_type: str, tenant_id: str, payload: dict) -> None:
    task = asyncio.create_task(publish_event("auth.security", event_type, tenant_id, payload))
    _pending_events.add(task)
    task.add_done_callback(_pending_events.discard)

router = APIRouter()
settings = get_settings()

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


def _client_meta(request: Request) -> tuple[str | None, str | None]:
    return (request.client.host if request.client else None), request.headers.get("user-agent")


def _set_auth_cookies(response: Response, tokens: auth_engine.TokenPair) -> None:
    response.set_cookie(
        ACCESS_COOKIE, tokens.access_token, httponly=True, secure=settings.cookie_secure,
        samesite="lax", domain=settings.cookie_domain, max_age=settings.access_token_ttl_minutes * 60,
    )
    response.set_cookie(
        REFRESH_COOKIE, tokens.refresh_token, httponly=True, secure=settings.cookie_secure,
        samesite="lax", domain=settings.cookie_domain,
        max_age=settings.refresh_token_ttl_days * 86400,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, domain=settings.cookie_domain)
    response.delete_cookie(REFRESH_COOKIE, domain=settings.cookie_domain)


class RegisterIn(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class MfaVerifyIn(BaseModel):
    user_credential_id: uuid.UUID
    totp_code: str


class MfaConfirmIn(BaseModel):
    totp_code: str


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetIn(BaseModel):
    token: str
    new_password: str


@router.post("/register", status_code=201)
async def register(payload: RegisterIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    if len(payload.password) < 10:
        raise HTTPException(status_code=422, detail="Password must be at least 10 characters")
    try:
        cred = await auth_engine.register_user(db, tenant_id=tenant_id, user_id=payload.user_id, email=payload.email, password=payload.password)
    except auth_engine.CredentialExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    _emit("auth.credential_registered", tenant_id, {"user_credential_id": str(cred.id), "user_id": str(payload.user_id)})
    return {"user_credential_id": cred.id, "email": cred.email}


@router.post("/login")
async def login(payload: LoginIn, request: Request, response: Response, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    ip_address, user_agent = _client_meta(request)
    outcome = await auth_engine.login(db, tenant_id=tenant_id, email=payload.email, password=payload.password, ip_address=ip_address, user_agent=user_agent)
    await db.commit()
    login_event = {
        "success": "auth.login_succeeded", "mfa_required": "auth.login_mfa_challenged",
        "locked": "auth.account_locked",
    }.get(outcome.status, "auth.login_failed")
    _emit(login_event, tenant_id, {
        "email": payload.email, "user_credential_id": str(outcome.user_credential_id) if outcome.user_credential_id else None,
        "ip_address": ip_address, "failure_reason": outcome.failure_reason,
    })

    if outcome.status == "success":
        _set_auth_cookies(response, outcome.tokens)
        return {"status": "success"}
    if outcome.status == "mfa_required":
        return {"status": "mfa_required", "user_credential_id": outcome.user_credential_id}
    if outcome.status == "locked":
        raise HTTPException(status_code=423, detail={"message": "Account temporarily locked", "locked_until": outcome.locked_until.isoformat()})
    raise HTTPException(status_code=401, detail="Invalid email or password")


@router.post("/mfa/verify")
async def mfa_verify(payload: MfaVerifyIn, request: Request, response: Response, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    ip_address, user_agent = _client_meta(request)
    outcome = await auth_engine.verify_mfa_and_issue_tokens(
        db, tenant_id=tenant_id, user_credential_id=payload.user_credential_id, totp_code=payload.totp_code,
        ip_address=ip_address, user_agent=user_agent,
    )
    await db.commit()

    if outcome.status == "success":
        _set_auth_cookies(response, outcome.tokens)
        return {"status": "success"}
    if outcome.status == "locked":
        raise HTTPException(status_code=423, detail={"message": "Account temporarily locked", "locked_until": outcome.locked_until.isoformat()})
    raise HTTPException(status_code=401, detail="Invalid authentication code")


@router.post("/mfa/enroll")
async def mfa_enroll(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    uri = await auth_engine.enroll_mfa(db, user_credential_id=uuid.UUID(user["sub"]), tenant_id=user["tenant_id"])
    await db.commit()
    return {"provisioning_uri": uri}


@router.post("/mfa/confirm")
async def mfa_confirm(payload: MfaConfirmIn, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    confirmed = await auth_engine.confirm_mfa_enrollment(db, user_credential_id=uuid.UUID(user["sub"]), tenant_id=user["tenant_id"], totp_code=payload.totp_code)
    await db.commit()
    if not confirmed:
        raise HTTPException(status_code=400, detail="Invalid authentication code")
    _emit("auth.mfa_enabled", user["tenant_id"], {"user_credential_id": user["sub"], "user_id": user["user_id"]})
    return {"mfa_enabled": True}


@router.post("/refresh")
async def refresh(request: Request, response: Response, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    raw_refresh = request.cookies.get(REFRESH_COOKIE)
    if not raw_refresh:
        raise HTTPException(status_code=401, detail="No refresh token presented")
    ip_address, user_agent = _client_meta(request)
    try:
        new_tokens = await auth_engine.refresh_tokens(db, tenant_id=tenant_id, raw_refresh_token=raw_refresh, ip_address=ip_address, user_agent=user_agent)
    except auth_engine.ReuseDetectedError as exc:
        await db.commit()
        # Suspected theft: kill every access token already issued too,
        # not just the refresh tokens.
        await revocation.revoke_all_for_credential(tenant_id, exc.user_credential_id)
        _emit("auth.refresh_token_reuse_detected", tenant_id, {"ip_address": ip_address})
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Session revoked — possible token theft detected, please log in again")
    except ValueError:
        await db.rollback()
        _clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    await db.commit()
    _set_auth_cookies(response, new_tokens)
    return {"status": "success"}


@router.post("/logout")
async def logout(request: Request, response: Response, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    raw_refresh = request.cookies.get(REFRESH_COOKIE)
    if raw_refresh:
        await auth_engine.logout(db, tenant_id=tenant_id, raw_refresh_token=raw_refresh)
        await db.commit()
    raw_access = request.cookies.get(ACCESS_COOKIE)
    if raw_access:
        try:
            claims = decode_access_token(raw_access)
        except Exception:  # noqa: BLE001 — an already-invalid token needs no revoking
            claims = None
        if claims:
            await revocation.revoke_token(claims.get("jti"), claims.get("exp"))
    _clear_auth_cookies(response)
    return {"status": "success"}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {"user_credential_id": user["sub"], "user_id": user["user_id"], "tenant_id": user["tenant_id"]}


@router.post("/password-reset/request")
async def password_reset_request(payload: PasswordResetRequestIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    raw_token = await auth_engine.request_password_reset(db, tenant_id=tenant_id, email=payload.email)
    await db.commit()
    if raw_token:
        from src.services.notification_client import send_password_reset_email
        await send_password_reset_email(tenant_id=tenant_id, email=payload.email, raw_token=raw_token)
    # Always the same response, regardless of whether the email existed.
    return {"status": "if_account_exists_email_sent"}


@router.post("/password-reset/confirm")
async def password_reset_confirm(payload: PasswordResetIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    if len(payload.new_password) < 10:
        raise HTTPException(status_code=422, detail="Password must be at least 10 characters")
    credential_id = await auth_engine.credential_id_for_reset_token(db, tenant_id=tenant_id, raw_reset_token=payload.token)
    ok = await auth_engine.reset_password(db, tenant_id=tenant_id, raw_reset_token=payload.token, new_password=payload.new_password)
    await db.commit()
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    await revocation.revoke_all_for_credential(tenant_id, credential_id)
    _emit("auth.password_reset_completed", tenant_id, {})
    return {"status": "success"}
