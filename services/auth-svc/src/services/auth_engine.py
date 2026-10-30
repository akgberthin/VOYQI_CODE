"""Authentication engine.

Every security-relevant decision here is a deterministic, fixed rule —
account lockout thresholds, token rotation, reuse detection — not
something an AI agent proposes or a human approves case by case. This
mirrors the platform-wide posture: judgment calls get a human or an
agent's reasoning; fixed security mechanics get code.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pyotp
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.security import hash_password, verify_password
from src.core.tokens import generate_opaque_token, hash_token, issue_access_token
from src.models import (
    FailureReason,
    LoginAuditLog,
    PasswordResetToken,
    RefreshToken,
    UserCredential,
)

settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip even for timezone(True) columns —
    normalize before comparing against utcnow(), or every naive-vs-aware
    comparison here raises TypeError. Postgres doesn't have this problem,
    but this must work identically against both."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime


@dataclass
class LoginOutcome:
    status: str  # "success" | "mfa_required" | "failed" | "locked"
    tokens: TokenPair | None = None
    user_credential_id: uuid.UUID | None = None
    failure_reason: str | None = None
    locked_until: datetime | None = None


class ReuseDetectedError(Exception):
    """Raised when a refresh token that was already rotated (revoked) is
    presented again — the standard signal of token theft. Callers should
    treat this as a security incident, not an ordinary auth failure."""

    def __init__(self, message: str, user_credential_id: uuid.UUID | None = None):
        super().__init__(message)
        self.user_credential_id = user_credential_id


async def _log_attempt(
    db: AsyncSession, *, tenant_id: str, email: str, success: bool,
    user_credential_id: uuid.UUID | None = None, failure_reason: str | None = None,
    ip_address: str | None = None, user_agent: str | None = None,
) -> None:
    db.add(LoginAuditLog(
        tenant_id=tenant_id, email_attempted=email, success=success,
        user_credential_id=user_credential_id, failure_reason=failure_reason,
        ip_address=ip_address, user_agent=user_agent,
    ))


async def _issue_token_pair(db: AsyncSession, *, cred: UserCredential, ip_address: str | None, user_agent: str | None) -> TokenPair:
    access_token = issue_access_token(user_credential_id=cred.id, user_id=cred.user_id, tenant_id=cred.tenant_id)
    raw_refresh = generate_opaque_token()
    expires_at = utcnow() + timedelta(days=settings.refresh_token_ttl_days)
    db.add(RefreshToken(
        tenant_id=cred.tenant_id, user_credential_id=cred.id,
        token_hash=hash_token(raw_refresh), expires_at=expires_at,
        ip_address=ip_address, user_agent=user_agent,
    ))
    return TokenPair(access_token=access_token, refresh_token=raw_refresh, refresh_expires_at=expires_at)


class CredentialExistsError(Exception):
    pass


async def register_user(db: AsyncSession, *, tenant_id: str, user_id: uuid.UUID, email: str, password: str) -> UserCredential:
    email_norm = email.lower().strip()
    existing = await db.execute(select(UserCredential.id).where(UserCredential.tenant_id == tenant_id, UserCredential.email == email_norm))
    if existing.first() is not None:
        raise CredentialExistsError("an account with this email already exists")
    cred = UserCredential(tenant_id=tenant_id, user_id=user_id, email=email_norm, password_hash=hash_password(password))
    db.add(cred)
    await db.flush()
    return cred


async def login(
    db: AsyncSession, *, tenant_id: str, email: str, password: str,
    ip_address: str | None = None, user_agent: str | None = None,
) -> LoginOutcome:
    email_norm = email.lower().strip()
    result = await db.execute(select(UserCredential).where(UserCredential.tenant_id == tenant_id, UserCredential.email == email_norm))
    cred = result.scalar_one_or_none()

    if cred is None:
        await _log_attempt(db, tenant_id=tenant_id, email=email_norm, success=False, failure_reason=FailureReason.UNKNOWN_EMAIL)
        # Generic failure — never reveal whether the email exists.
        return LoginOutcome(status="failed", failure_reason="invalid_credentials")

    if not cred.is_active:
        await _log_attempt(db, tenant_id=tenant_id, email=email_norm, success=False, user_credential_id=cred.id, failure_reason=FailureReason.ACCOUNT_INACTIVE)
        return LoginOutcome(status="failed", failure_reason="invalid_credentials")

    if cred.locked_until and _aware(cred.locked_until) > utcnow():
        await _log_attempt(db, tenant_id=tenant_id, email=email_norm, success=False, user_credential_id=cred.id, failure_reason=FailureReason.ACCOUNT_LOCKED)
        return LoginOutcome(status="locked", user_credential_id=cred.id, locked_until=cred.locked_until, failure_reason="account_locked")

    if not verify_password(password, cred.password_hash):
        cred.failed_login_attempts += 1
        locked_now = False
        if cred.failed_login_attempts >= settings.max_failed_login_attempts:
            cred.locked_until = utcnow() + timedelta(minutes=settings.lockout_duration_minutes)
            locked_now = True
        await _log_attempt(db, tenant_id=tenant_id, email=email_norm, success=False, user_credential_id=cred.id, failure_reason=FailureReason.INVALID_PASSWORD)
        if locked_now:
            return LoginOutcome(status="locked", user_credential_id=cred.id, locked_until=cred.locked_until, failure_reason="account_locked")
        return LoginOutcome(status="failed", failure_reason="invalid_credentials")

    # Password correct from here on.
    if cred.mfa_enabled:
        await _log_attempt(db, tenant_id=tenant_id, email=email_norm, success=False, user_credential_id=cred.id, failure_reason=None)
        return LoginOutcome(status="mfa_required", user_credential_id=cred.id)

    cred.failed_login_attempts = 0
    cred.locked_until = None
    cred.last_login_at = utcnow()
    tokens = await _issue_token_pair(db, cred=cred, ip_address=ip_address, user_agent=user_agent)
    await _log_attempt(db, tenant_id=tenant_id, email=email_norm, success=True, user_credential_id=cred.id)
    return LoginOutcome(status="success", tokens=tokens, user_credential_id=cred.id)


async def verify_mfa_and_issue_tokens(
    db: AsyncSession, *, tenant_id: str, user_credential_id: uuid.UUID, totp_code: str,
    ip_address: str | None = None, user_agent: str | None = None,
) -> LoginOutcome:
    result = await db.execute(select(UserCredential).where(UserCredential.id == user_credential_id, UserCredential.tenant_id == tenant_id))
    cred = result.scalar_one_or_none()
    if cred is None or not cred.mfa_enabled or not cred.mfa_secret:
        return LoginOutcome(status="failed", failure_reason="invalid_credentials")

    if cred.locked_until and _aware(cred.locked_until) > utcnow():
        return LoginOutcome(status="locked", user_credential_id=cred.id, locked_until=cred.locked_until, failure_reason="account_locked")

    totp = pyotp.TOTP(cred.mfa_secret)
    if not totp.verify(totp_code, valid_window=1):
        cred.failed_login_attempts += 1
        locked_now = False
        if cred.failed_login_attempts >= settings.max_failed_login_attempts:
            cred.locked_until = utcnow() + timedelta(minutes=settings.lockout_duration_minutes)
            locked_now = True
        await _log_attempt(db, tenant_id=tenant_id, email=cred.email, success=False, user_credential_id=cred.id, failure_reason=FailureReason.INVALID_MFA)
        if locked_now:
            return LoginOutcome(status="locked", user_credential_id=cred.id, locked_until=cred.locked_until, failure_reason="account_locked")
        return LoginOutcome(status="failed", failure_reason="invalid_mfa_code")

    cred.failed_login_attempts = 0
    cred.locked_until = None
    cred.last_login_at = utcnow()
    tokens = await _issue_token_pair(db, cred=cred, ip_address=ip_address, user_agent=user_agent)
    await _log_attempt(db, tenant_id=tenant_id, email=cred.email, success=True, user_credential_id=cred.id)
    return LoginOutcome(status="success", tokens=tokens, user_credential_id=cred.id)


async def enroll_mfa(db: AsyncSession, *, user_credential_id: uuid.UUID, tenant_id: str) -> str:
    """Generates a new TOTP secret and returns its provisioning URI (for a
    QR code). MFA is not yet *enabled* until confirm_mfa_enrollment
    verifies the user can actually produce a valid code with it."""
    result = await db.execute(select(UserCredential).where(UserCredential.id == user_credential_id, UserCredential.tenant_id == tenant_id))
    cred = result.scalar_one()
    secret = pyotp.random_base32()
    cred.mfa_pending_secret = secret
    return pyotp.TOTP(secret).provisioning_uri(name=cred.email, issuer_name="VOYQI")


async def confirm_mfa_enrollment(db: AsyncSession, *, user_credential_id: uuid.UUID, tenant_id: str, totp_code: str) -> bool:
    result = await db.execute(select(UserCredential).where(UserCredential.id == user_credential_id, UserCredential.tenant_id == tenant_id))
    cred = result.scalar_one()
    secret = cred.mfa_pending_secret or (cred.mfa_secret if not cred.mfa_enabled else None)
    if not secret:
        return False
    if not pyotp.TOTP(secret).verify(totp_code, valid_window=1):
        return False
    cred.mfa_secret = secret
    cred.mfa_pending_secret = None
    cred.mfa_enabled = True
    return True


async def refresh_tokens(
    db: AsyncSession, *, tenant_id: str, raw_refresh_token: str,
    ip_address: str | None = None, user_agent: str | None = None,
) -> TokenPair:
    """Rotates the refresh token on every use. If the presented token was
    already revoked (i.e. someone is replaying an old, rotated-out token —
    the standard theft signal), every refresh token for that user is
    revoked immediately rather than silently failing."""
    token_hash = hash_token(raw_refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash, RefreshToken.tenant_id == tenant_id))
    token_row = result.scalar_one_or_none()

    if token_row is None:
        raise ValueError("invalid refresh token")

    if token_row.revoked_at is not None:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_credential_id == token_row.user_credential_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        raise ReuseDetectedError(
            f"revoked refresh token reused for user_credential_id={token_row.user_credential_id}", token_row.user_credential_id,
        )

    if _aware(token_row.expires_at) < utcnow():
        raise ValueError("expired refresh token")

    cred_result = await db.execute(select(UserCredential).where(UserCredential.id == token_row.user_credential_id))
    cred = cred_result.scalar_one()

    new_pair = await _issue_token_pair(db, cred=cred, ip_address=ip_address, user_agent=user_agent)
    await db.flush()  # so the new RefreshToken row has an id to link as replaced_by
    new_row_result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(new_pair.refresh_token)))
    new_row = new_row_result.scalar_one()

    token_row.revoked_at = utcnow()
    token_row.replaced_by_id = new_row.id
    return new_pair


async def logout(db: AsyncSession, *, tenant_id: str, raw_refresh_token: str) -> None:
    token_hash = hash_token(raw_refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash, RefreshToken.tenant_id == tenant_id))
    token_row = result.scalar_one_or_none()
    if token_row is not None and token_row.revoked_at is None:
        token_row.revoked_at = utcnow()


async def request_password_reset(db: AsyncSession, *, tenant_id: str, email: str) -> str | None:
    """Always returns without error, even for an unknown email (avoid
    revealing whether an account exists). Returns the raw reset token
    ONLY so the caller (the API layer) can hand it to notification-svc —
    it is never itself returned to the HTTP client."""
    email_norm = email.lower().strip()
    result = await db.execute(select(UserCredential).where(UserCredential.tenant_id == tenant_id, UserCredential.email == email_norm, UserCredential.is_active.is_(True)))
    cred = result.scalar_one_or_none()
    if cred is None:
        return None

    raw_token = generate_opaque_token()
    db.add(PasswordResetToken(
        tenant_id=tenant_id, user_credential_id=cred.id,
        token_hash=hash_token(raw_token), expires_at=utcnow() + timedelta(minutes=settings.password_reset_ttl_minutes),
    ))
    return raw_token


async def credential_id_for_reset_token(db: AsyncSession, *, tenant_id: str, raw_reset_token: str) -> uuid.UUID | None:
    result = await db.execute(
        select(PasswordResetToken.user_credential_id).where(
            PasswordResetToken.token_hash == hash_token(raw_reset_token), PasswordResetToken.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


async def reset_password(db: AsyncSession, *, tenant_id: str, raw_reset_token: str, new_password: str) -> bool:
    token_hash = hash_token(raw_reset_token)
    result = await db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash, PasswordResetToken.tenant_id == tenant_id))
    token_row = result.scalar_one_or_none()
    if token_row is None or token_row.used_at is not None or _aware(token_row.expires_at) < utcnow():
        return False

    cred_result = await db.execute(select(UserCredential).where(UserCredential.id == token_row.user_credential_id))
    cred = cred_result.scalar_one()
    cred.password_hash = hash_password(new_password)
    cred.failed_login_attempts = 0
    cred.locked_until = None
    token_row.used_at = utcnow()

    # Password change invalidates every existing session — force re-login
    # everywhere, standard practice in case the password was compromised.
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_credential_id == cred.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    return True
