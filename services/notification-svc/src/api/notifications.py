import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event

DELIVERY_EVENT = {"sent": "notification.sent", "failed": "notification.failed", "skipped_opted_out": "notification.skipped_opted_out"}
from src.core.security import get_tenant_id
from src.models import Notification, NotificationTemplate
from src.services import notification_engine, template_seed

router = APIRouter()


class SendIn(BaseModel):
    channel: str
    template: str
    recipient: str
    variables: dict = {}
    category: str | None = None
    user_id: uuid.UUID | None = None


class DirectIn(BaseModel):
    channel: str
    recipient: str
    body: str
    subject: str | None = None
    category: str = "marketing"
    source: str = "direct"
    user_id: uuid.UUID | None = None


class PreferenceIn(BaseModel):
    user_id: uuid.UUID
    category: str
    channel: str
    opted_in: bool


@router.post("/send", status_code=201)
async def send(payload: SendIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        notification = await notification_engine.send_notification(
            db, tenant_id=tenant_id, channel=payload.channel, template_key=payload.template,
            recipient=payload.recipient, variables=payload.variables, category=payload.category, user_id=payload.user_id,
        )
    except notification_engine.TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except notification_engine.TemplateRenderError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    # Recipient address is deliberately left out of the event — consumers
    # get the delivery outcome, not the contact detail.
    background_tasks.add_task(
        publish_event, "notification.delivery", DELIVERY_EVENT.get(notification.status, "notification.queued"), tenant_id,
        {
            "notification_id": str(notification.id), "channel": payload.channel, "template": payload.template,
            "category": payload.category, "user_id": str(payload.user_id) if payload.user_id else None,
            "status": notification.status, "error_message": notification.error_message,
        },
    )
    return {
        "id": notification.id, "status": notification.status, "delivery_mode": notification.delivery_mode,
        "error_message": notification.error_message,
    }


@router.post("/send-direct", status_code=201)
async def send_direct(payload: DirectIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Deliver content another service already wrote (e.g. a marketing
    send) on email, sms, push or voice — no template involved."""
    try:
        notification = await notification_engine.send_direct(
            db, tenant_id=tenant_id, channel=payload.channel, recipient=payload.recipient, body=payload.body,
            subject=payload.subject, category=payload.category, user_id=payload.user_id, source=payload.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    background_tasks.add_task(
        publish_event, "notification.delivery", DELIVERY_EVENT.get(notification.status, "notification.queued"), tenant_id,
        {"notification_id": str(notification.id), "channel": payload.channel, "template": payload.source,
         "category": payload.category, "status": notification.status, "error_message": notification.error_message},
    )
    return {"id": notification.id, "status": notification.status, "delivery_mode": notification.delivery_mode,
            "provider_message_id": notification.provider_message_id, "error_message": notification.error_message}


@router.get("/notifications")
async def list_notifications(recipient: str | None = None, status: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    query = select(Notification).where(Notification.tenant_id == tenant_id).order_by(Notification.created_at.desc()).limit(200)
    if recipient:
        query = query.where(Notification.recipient == recipient)
    if status:
        query = query.where(Notification.status == status)
    result = await db.execute(query)
    rows = result.scalars().all()
    return [{"id": r.id, "channel": r.channel, "template_key": r.template_key, "recipient": r.recipient, "status": r.status, "delivery_mode": r.delivery_mode, "created_at": r.created_at} for r in rows]


@router.post("/templates/seed-defaults")
async def seed_defaults(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    count = await template_seed.seed_default_templates(db, tenant_id=tenant_id)
    await db.commit()
    return {"templates_inserted": count}


@router.get("/templates")
async def list_templates(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(NotificationTemplate).where(NotificationTemplate.tenant_id == tenant_id))
    rows = result.scalars().all()
    return [{"id": r.id, "key": r.key, "channel": r.channel, "category": r.category, "is_active": r.is_active} for r in rows]


@router.put("/preferences")
async def set_preference(payload: PreferenceIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        pref = await notification_engine.set_preference(
            db, tenant_id=tenant_id, user_id=payload.user_id, category=payload.category,
            channel=payload.channel, opted_in=payload.opted_in,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {"id": pref.id, "opted_in": pref.opted_in}
