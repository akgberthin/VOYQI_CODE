"""What providers report after a message leaves: Twilio posts each text's
delivery and each call's outcome here (public — no session, verified by
Twilio's signature with the tenant's auth token from super admin →
Integrations). Callers read the outcomes back with POST /statuses."""
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import integrations
from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import Notification
from src.services import providers

public_router = APIRouter(prefix="/api/v1/notifications/public", tags=["provider-callbacks"])
router = APIRouter(prefix="/api/v1/notifications", tags=["provider-callbacks"])


@public_router.post("/twilio/{tenant_id}/status")
async def twilio_status(tenant_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    c = await integrations.get(tenant_id, "twilio")
    url = providers.status_callback_url(c or {}, tenant_id)
    params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    if not c or not url or not providers.twilio_signature_valid(c.get("auth_token", ""), url, params, request.headers.get("X-Twilio-Signature", "")):
        raise HTTPException(status_code=403, detail="signature check failed")
    sid = params.get("MessageSid") or params.get("CallSid") or ""
    row = (await db.execute(select(Notification).where(Notification.tenant_id == tenant_id, Notification.provider_message_id == sid))).scalar_one_or_none() if sid else None
    if row is None:
        return Response(status_code=204)  # not ours (or already gone): nothing to update
    if params.get("RecordingUrl"):
        row.recording_url = params["RecordingUrl"]
    status = params.get("MessageStatus") or params.get("CallStatus")
    if status:
        row.provider_status = status[:32]
    if params.get("AnsweredBy"):
        row.answered_by = params["AnsweredBy"][:32]
    if (params.get("CallDuration") or "").isdigit():
        row.duration_seconds = int(params["CallDuration"])
    if status in ("undelivered", "failed") and params.get("ErrorCode"):
        row.error_message = f"Twilio error {params['ErrorCode']}: {params.get('ErrorMessage', '')}".strip()[:500]
    row.status_updated_at = datetime.now(timezone.utc)
    await db.commit()
    return Response(status_code=204)


class StatusesIn(BaseModel):
    ids: list[uuid.UUID]


@router.post("/statuses")
async def statuses(body: StatusesIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The outcome of each given notification — what marketing polls for
    voice pickups, retries and delivered texts."""
    if not body.ids:
        return []
    rows = (await db.execute(select(Notification).where(Notification.tenant_id == tenant_id, or_(*[Notification.id == i for i in body.ids[:500]])))).scalars()
    return [{"id": str(r.id), "channel": r.channel, "status": r.status, "delivery_mode": r.delivery_mode, "provider_status": r.provider_status,
             "answered_by": r.answered_by, "duration_seconds": r.duration_seconds, "recording_url": r.recording_url,
             "status_updated_at": r.status_updated_at} for r in rows]
