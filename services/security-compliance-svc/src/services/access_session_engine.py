"""Privileged-access sessions: break-glass and impersonation.

Starting either is never blocked here — break-glass exists for
emergencies — but every start publishes an access.* event the
Compliance/Audit Agent scores (off-hours, long windows, no session
recording). When the agent's risk score is high it queues a
recommendation to end the session; a human approving that
recommendation calls end_* below through ai-orchestration-svc.
"""
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.kafka_events import publish_event
from src.models import AccessSessionStatus, BreakGlassSession, ImpersonationSession


class ReasonRequiredError(Exception):
    pass


class InvalidDurationError(Exception):
    pass


class SessionAlreadyEndedError(Exception):
    pass


def is_off_hours(moment: datetime) -> bool:
    settings = get_settings()
    local = moment.astimezone(ZoneInfo(settings.business_timezone))
    if local.weekday() >= 5:
        return True
    return not (settings.business_hours_start <= local.hour < settings.business_hours_end)


def _require_reason(reason: str) -> str:
    if not reason or not reason.strip():
        raise ReasonRequiredError("a reason is required to start a privileged access session")
    return reason.strip()


async def start_break_glass(
    db: AsyncSession, tenant_id: str, user_id: str, activated_by: str, reason: str, duration_minutes: int,
    now: datetime | None = None,
) -> BreakGlassSession:
    reason = _require_reason(reason)
    max_minutes = get_settings().break_glass_max_minutes
    if not 1 <= duration_minutes <= max_minutes:
        raise InvalidDurationError(f"duration_minutes must be between 1 and {max_minutes}")
    now = now or datetime.now(timezone.utc)
    session = BreakGlassSession(
        tenant_id=tenant_id, user_id=user_id, activated_by=activated_by, reason=reason,
        duration_minutes=duration_minutes, off_hours=is_off_hours(now),
        started_at=now, expires_at=now + timedelta(minutes=duration_minutes),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    await publish_event(
        "access.break_glass_activated",
        {
            "tenant_id": tenant_id, "session_id": str(session.id), "user_id": user_id,
            "activated_by": activated_by, "reason": reason,
            "duration_minutes": duration_minutes, "off_hours": session.off_hours,
        },
    )
    return session


async def start_impersonation(
    db: AsyncSession, tenant_id: str, admin_user_id: str, target_user_id: str, reason: str,
    session_recording_ref: str | None = None, now: datetime | None = None,
) -> ImpersonationSession:
    reason = _require_reason(reason)
    now = now or datetime.now(timezone.utc)
    session = ImpersonationSession(
        tenant_id=tenant_id, admin_user_id=admin_user_id, target_user_id=target_user_id, reason=reason,
        session_recording_ref=session_recording_ref or None, off_hours=is_off_hours(now), started_at=now,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    await publish_event(
        "access.impersonation_started",
        {
            "tenant_id": tenant_id, "session_id": str(session.id),
            # user_id is the actor — the Compliance/Audit Agent looks up
            # *their* RBAC context, not the impersonated user's.
            "user_id": admin_user_id, "target_user_id": target_user_id, "reason": reason,
            "has_session_recording": bool(session.session_recording_ref), "off_hours": session.off_hours,
        },
    )
    return session


async def _end(db: AsyncSession, model, tenant_id: str, session_id: uuid.UUID, ended_by: str, reason: str, event_type: str):
    result = await db.execute(select(model).where(model.id == session_id, model.tenant_id == tenant_id))
    session = result.scalar_one_or_none()
    if session is None:
        return None
    if session.status == AccessSessionStatus.ENDED:
        raise SessionAlreadyEndedError(f"session {session_id} has already ended")
    session.status = AccessSessionStatus.ENDED
    session.ended_at = datetime.now(timezone.utc)
    session.ended_by = ended_by
    session.end_reason = reason
    await db.commit()
    await db.refresh(session)
    await publish_event(event_type, {"tenant_id": tenant_id, "session_id": str(session.id), "ended_by": ended_by, "reason": reason})
    return session


async def end_break_glass(db: AsyncSession, tenant_id: str, session_id: uuid.UUID, ended_by: str, reason: str):
    return await _end(db, BreakGlassSession, tenant_id, session_id, ended_by, reason, "access.break_glass_ended")


async def end_impersonation(db: AsyncSession, tenant_id: str, session_id: uuid.UUID, ended_by: str, reason: str):
    return await _end(db, ImpersonationSession, tenant_id, session_id, ended_by, reason, "access.impersonation_ended")


async def list_sessions(db: AsyncSession, model, tenant_id: str, status: AccessSessionStatus | None = None) -> list:
    query = select(model).where(model.tenant_id == tenant_id)
    if status is not None:
        query = query.where(model.status == status)
    result = await db.execute(query.order_by(model.started_at.desc()))
    return list(result.scalars().all())
