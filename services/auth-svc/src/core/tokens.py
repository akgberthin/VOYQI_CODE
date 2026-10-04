"""JWT issuance/verification and secure random token generation.

Access tokens are short-lived JWTs. Refresh and password-reset tokens are
opaque random strings — only their SHA-256 hash is ever persisted, so a
database read alone can never yield a usable token (matches the
platform's "credentials are never recoverable, only rotatable" posture).
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from src.core.config import get_settings

settings = get_settings()


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_opaque_token() -> str:
    """A cryptographically random, URL-safe token for refresh/reset flows."""
    return secrets.token_urlsafe(48)


def issue_access_token(*, user_credential_id: uuid.UUID, user_id: uuid.UUID, tenant_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_credential_id),
        "user_id": str(user_id),
        "tenant_id": tenant_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises jwt exceptions (ExpiredSignatureError, InvalidTokenError, etc.)
    on failure — callers are expected to catch and translate to a 401."""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return payload
