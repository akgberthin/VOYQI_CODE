import uuid

import pytest

from src.models import NotificationCategory, NotificationTemplate
from src.services import notification_engine

TENANT = "voyqi-holdings"


async def _add_template(db_session, key="welcome_member", channel="email", category=NotificationCategory.TRANSACTIONAL.value, subject="Hi {{ name }}", body="<p>Hello {{ name }}, welcome.</p>"):
    template = NotificationTemplate(tenant_id=TENANT, key=key, channel=channel, category=category, subject_template=subject, body_template=body)
    db_session.add(template)
    await db_session.flush()
    return template


@pytest.mark.asyncio
async def test_render_template_substitutes_variables():
    rendered = notification_engine.render_template("Hello {{ name }}, your code is {{ code }}", {"name": "Garth", "code": "123456"})
    assert rendered == "Hello Garth, your code is 123456"


@pytest.mark.asyncio
async def test_render_template_missing_variable_raises_clear_error():
    with pytest.raises(notification_engine.TemplateRenderError):
        notification_engine.render_template("Hello {{ name }}", {})


@pytest.mark.asyncio
async def test_send_notification_unknown_template_raises(db_session):
    with pytest.raises(notification_engine.TemplateNotFoundError):
        await notification_engine.send_notification(db_session, tenant_id=TENANT, channel="email", template_key="does_not_exist", recipient="a@voyqi.com")


@pytest.mark.asyncio
async def test_send_notification_console_mode_when_no_smtp_configured(db_session):
    """No SMTP host is configured in this test environment — delivery
    must honestly report console mode, not claim a real send."""
    await _add_template(db_session)
    await db_session.commit()

    notification = await notification_engine.send_notification(
        db_session, tenant_id=TENANT, channel="email", template_key="welcome_member",
        recipient="member@voyqi.com", variables={"name": "Garth"},
    )
    await db_session.commit()

    assert notification.status == "sent"
    assert notification.delivery_mode == "console"
    assert notification.subject == "Hi Garth"
    assert "Hello Garth, welcome." in notification.body


@pytest.mark.asyncio
async def test_security_category_notification_always_sends_even_when_opted_out(db_session):
    await _add_template(db_session, key="security_alert", category=NotificationCategory.SECURITY.value, subject="Alert", body="<p>{{ msg }}</p>")
    await db_session.commit()
    user_id = uuid.uuid4()

    # Try (and fail, by design) to opt out of security notifications.
    with pytest.raises(ValueError):
        await notification_engine.set_preference(db_session, tenant_id=TENANT, user_id=user_id, category=NotificationCategory.SECURITY.value, channel="email", opted_in=False)
    await db_session.rollback()

    notification = await notification_engine.send_notification(
        db_session, tenant_id=TENANT, channel="email", template_key="security_alert",
        recipient="member@voyqi.com", variables={"msg": "New device login"}, user_id=user_id,
    )
    assert notification.status == "sent"


@pytest.mark.asyncio
async def test_transactional_notification_is_skipped_when_opted_out(db_session):
    await _add_template(db_session, key="marketing_promo", category=NotificationCategory.MARKETING.value, subject="Sale!", body="<p>Save now</p>")
    await db_session.commit()
    user_id = uuid.uuid4()

    await notification_engine.set_preference(db_session, tenant_id=TENANT, user_id=user_id, category=NotificationCategory.MARKETING.value, channel="email", opted_in=False)
    await db_session.commit()

    notification = await notification_engine.send_notification(
        db_session, tenant_id=TENANT, channel="email", template_key="marketing_promo",
        recipient="member@voyqi.com", user_id=user_id,
    )
    await db_session.commit()
    assert notification.status == "skipped_opted_out"


@pytest.mark.asyncio
async def test_opted_in_after_previously_opted_out_now_sends(db_session):
    await _add_template(db_session, key="marketing_promo2", category=NotificationCategory.MARKETING.value, subject="Sale!", body="<p>Save now</p>")
    await db_session.commit()
    user_id = uuid.uuid4()

    await notification_engine.set_preference(db_session, tenant_id=TENANT, user_id=user_id, category=NotificationCategory.MARKETING.value, channel="email", opted_in=False)
    await notification_engine.set_preference(db_session, tenant_id=TENANT, user_id=user_id, category=NotificationCategory.MARKETING.value, channel="email", opted_in=True)
    await db_session.commit()

    notification = await notification_engine.send_notification(
        db_session, tenant_id=TENANT, channel="email", template_key="marketing_promo2",
        recipient="member@voyqi.com", user_id=user_id,
    )
    assert notification.status == "sent"


@pytest.mark.asyncio
async def test_notification_without_known_user_id_always_sends_regardless_of_category(db_session):
    """No user_id means there's no preference to check against — this
    matches system-initiated sends (e.g. a password reset before login)."""
    await _add_template(db_session, key="marketing_promo3", category=NotificationCategory.MARKETING.value, subject="Sale!", body="<p>Save now</p>")
    await db_session.commit()

    notification = await notification_engine.send_notification(
        db_session, tenant_id=TENANT, channel="email", template_key="marketing_promo3", recipient="member@voyqi.com",
    )
    assert notification.status == "sent"


@pytest.mark.asyncio
async def test_sms_without_provider_configured_is_console_mode(db_session):
    template = NotificationTemplate(tenant_id=TENANT, key="sms_test", channel="sms", category=NotificationCategory.TRANSACTIONAL.value, body_template="Your code: {{ code }}")
    db_session.add(template)
    await db_session.commit()

    notification = await notification_engine.send_notification(
        db_session, tenant_id=TENANT, channel="sms", template_key="sms_test", recipient="+15555550100", variables={"code": "999999"},
    )
    assert notification.status == "sent"
    assert notification.delivery_mode == "console"
