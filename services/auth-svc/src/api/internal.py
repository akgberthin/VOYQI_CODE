"""Account status for the Super Admin panel, reached only through rbac-svc.

The gateway lets any signed-in user call /api/v1/auth/*, so admin powers
(seeing who is locked out, lifting a lockout) can't live there. These
routes sit under /internal/, which the gateway never routes, and also
require the shared internal token.
"""
import hmac
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.database import get_db
from src.models import UserCredential

router = APIRouter(prefix="/internal/accounts", tags=["internal"])


def _require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    expected = get_settings().internal_token
    if not expected or not x_internal_token or not hmac.compare_digest(expected, x_internal_token):
        raise HTTPException(status_code=403, detail="internal endpoint")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _status(c: UserCredential, now: datetime) -> dict:
    locked_until = _aware(c.locked_until)
    locked = bool(locked_until and locked_until > now)
    return {
        "user_id": str(c.user_id),
        "email": c.email,
        "has_login": True,
        "locked": locked,
        "locked_until": locked_until.isoformat() if locked else None,
        "failed_login_attempts": c.failed_login_attempts or 0,
        "last_login_at": _aware(c.last_login_at).isoformat() if c.last_login_at else None,
        "mfa_enabled": bool(c.mfa_enabled),
        "is_active": bool(c.is_active),
    }


@router.get("", dependencies=[Depends(_require_internal)])
async def account_status(user_ids: list[uuid.UUID] = Query(default=[]), x_tenant_id: str = Header(...), db: AsyncSession = Depends(get_db)):
    """Lock state and last login for the given users (all of the tenant's when none given)."""
    q = select(UserCredential).where(UserCredential.tenant_id == x_tenant_id)
    if user_ids:
        q = q.where(UserCredential.user_id.in_(user_ids))
    now = datetime.now(timezone.utc)
    return [_status(c, now) for c in (await db.execute(q)).scalars().all()]


@router.post("/{user_id}/unlock", dependencies=[Depends(_require_internal)])
async def unlock(user_id: uuid.UUID, x_tenant_id: str = Header(...), db: AsyncSession = Depends(get_db)):
    """Lift a failed-sign-in lockout now instead of waiting it out."""
    rows = (await db.execute(select(UserCredential).where(UserCredential.tenant_id == x_tenant_id, UserCredential.user_id == user_id))).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="This user has no sign-in account.")
    for c in rows:
        c.failed_login_attempts = 0
        c.locked_until = None
    await db.commit()
    now = datetime.now(timezone.utc)
    return _status(rows[0], now)
