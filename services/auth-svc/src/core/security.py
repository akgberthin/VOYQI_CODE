import bcrypt
import jwt as pyjwt
from fastapi import Cookie, Header, HTTPException

from src.core.tokens import decode_access_token


async def get_tenant_id(x_tenant_id: str | None = Header(default=None)) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-Id header is required")
    return x_tenant_id


async def get_ai_initiated(x_ai_initiated: str | None = Header(default=None)) -> str | None:
    """Non-None when the request originated from ai-orchestration-svc rather
    than a human operator — audit rows record this distinction explicitly."""
    return x_ai_initiated


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash — never treat as a match.
        return False


async def get_current_user(access_token: str | None = Cookie(default=None)) -> dict:
    """Reads and verifies the access token JWT from its HttpOnly cookie —
    per the platform's non-negotiable rule, JWTs travel via HttpOnly
    cookies only, never a bearer header a client-side script could read.
    This is the dependency other services should mirror (same secret,
    same claim shape) to accept auth-svc-issued tokens directly."""
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return decode_access_token(access_token)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Access token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid access token")
