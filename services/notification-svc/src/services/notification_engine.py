"""Notification engine.

Opt-out is enforced here, once, not left to callers to remember: a
SECURITY-category notification always sends regardless of preference
state, by construction, not by convention.
"""
import uuid
from datetime import datetime, timezone

import jinja2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    Channel,
    Notification,
    NotificationCategory,
    NotificationPreference,
    NotificationStatus,
    NotificationTemplate,
)
from src.services import providers

_jinja_env = jinja2.Environment(autoescape=True, undefined=jinja2.StrictUndefined)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TemplateNotFoundError(Exception):
    pass


class TemplateRenderError(Exception):
    pass


def render_template(template_str: str, variables: dict) -> str:
    try:
        return _jinja_env.from_string(template_str).render(**variables)
    except jinja2.UndefinedError as exc:
        raise TemplateRenderError(f"missing template variable: {exc}") from exc


async def _is_opted_out(db: AsyncSession, *, tenant_id: str, user_id: uuid.UUID | None, category: str, channel: str) -> bool:
    if category == NotificationCategory.SECURITY.value:
        return False  # never opt-out-able
    if user_id is None:
        return False  # no known user to check a preference for
    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.tenant_id == tenant_id,
            NotificationPreference.user_id == user_id,
            NotificationPreference.category == category,
            NotificationPreference.channel == channel,
        )
    )
    pref = result.scalar_one_or_none()
    return pref is not None and not pref.opted_in


async def get_template(db: AsyncSession, *, tenant_id: str, key: str, channel: str) -> NotificationTemplate:
    result = await db.execute(
        select(NotificationTemplate).where(
            NotificationTemplate.tenant_id == tenant_id,
            NotificationTemplate.key == key,
            NotificationTemplate.channel == channel,
            NotificationTemplate.is_active.is_(True),
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise TemplateNotFoundError(f"no active template '{key}' for channel '{channel}' in tenant '{tenant_id}'")
    return template


async def send_notification(
    db: AsyncSession, *, tenant_id: str, channel: str, template_key: str, recipient: str,
    variables: dict | None = None, category: str | None = None, user_id: uuid.UUID | None = None,
) -> Notification:
    variables = variables or {}
    template = await get_template(db, tenant_id=tenant_id, key=template_key, channel=channel)
    effective_category = category or template.category

    subject = render_template(template.subject_template, variables) if template.subject_template else None
    body = render_template(template.body_template, variables)

    if await _is_opted_out(db, tenant_id=tenant_id, user_id=user_id, category=effective_category, channel=channel):
        notification = Notification(
            tenant_id=tenant_id, channel=channel, template_key=template_key, recipient=recipient, user_id=user_id,
            subject=subject, body=body, status=NotificationStatus.SKIPPED_OPTED_OUT.value,
        )
        db.add(notification)
        await db.flush()
        return notification

    return await _deliver(db, tenant_id=tenant_id, channel=channel, template_key=template_key, recipient=recipient,
                          user_id=user_id, subject=subject, body=body)


async def _deliver(db: AsyncSession, *, tenant_id: str, channel: str, template_key: str, recipient: str,
                   user_id: uuid.UUID | None, subject: str | None, body: str, options: dict | None = None) -> Notification:
    o = options or {}
    if channel == Channel.EMAIL.value:
        success, delivery_mode, provider_message_id, error = await providers.send_email(
            recipient=recipient, subject=subject or "", body=body, tenant_id=tenant_id,
            from_name=o.get("from_name"), reply_to=o.get("reply_to"), preview_text=o.get("preview_text"))
    elif channel == Channel.SMS.value:
        success, delivery_mode, provider_message_id, error = await providers.send_sms(recipient=recipient, body=body, tenant_id=tenant_id)
    elif channel == Channel.PUSH.value:
        success, delivery_mode, provider_message_id, error = await providers.send_push(
            recipient=recipient, body=body, subject=subject or "", tenant_id=tenant_id,
            data={k: str(o[k]) for k in ("deep_link", "notification_type") if o.get(k)})
    elif channel == Channel.VOICE.value:
        success, delivery_mode, provider_message_id, error = await providers.send_voice(
            recipient=recipient, body=body, tenant_id=tenant_id, voice=o.get("voice"), language=o.get("language"))
    else:
        raise ValueError(f"unsupported channel: {channel}")

    notification = Notification(
        tenant_id=tenant_id, channel=channel, template_key=template_key, recipient=recipient, user_id=user_id,
        subject=subject, body=body,
        status=NotificationStatus.SENT.value if success else NotificationStatus.FAILED.value,
        delivery_mode=delivery_mode, provider_message_id=provider_message_id, error_message=error,
        sent_at=utcnow() if success else None,
    )
    db.add(notification)
    await db.flush()
    return notification


async def send_direct(
    db: AsyncSession, *, tenant_id: str, channel: str, recipient: str, body: str, subject: str | None = None,
    category: str = "marketing", user_id: uuid.UUID | None = None, source: str = "direct", options: dict | None = None,
) -> Notification:
    """Already-written content (a marketing send) rather than a template.
    Opt-out preferences still apply. `options` carries channel settings:
    email from_name / reply_to / preview_text, push deep_link /
    notification_type, voice voice / language."""
    if await _is_opted_out(db, tenant_id=tenant_id, user_id=user_id, category=category, channel=channel):
        notification = Notification(
            tenant_id=tenant_id, channel=channel, template_key=source, recipient=recipient, user_id=user_id,
            subject=subject, body=body, status=NotificationStatus.SKIPPED_OPTED_OUT.value,
        )
        db.add(notification)
        await db.flush()
        return notification
    return await _deliver(db, tenant_id=tenant_id, channel=channel, template_key=source, recipient=recipient,
                          user_id=user_id, subject=subject, body=body, options=options)


async def set_preference(db: AsyncSession, *, tenant_id: str, user_id: uuid.UUID, category: str, channel: str, opted_in: bool) -> NotificationPreference:
    if category == NotificationCategory.SECURITY.value and not opted_in:
        raise ValueError("security notifications cannot be opted out of")
    result = await db.execute(
        select(NotificationPreference).where(
            NotificationPreference.tenant_id == tenant_id, NotificationPreference.user_id == user_id,
            NotificationPreference.category == category, NotificationPreference.channel == channel,
        )
    )
    pref = result.scalar_one_or_none()
    if pref is None:
        pref = NotificationPreference(tenant_id=tenant_id, user_id=user_id, category=category, channel=channel, opted_in=opted_in)
        db.add(pref)
    else:
        pref.opted_in = opted_in
    await db.flush()
    return pref
