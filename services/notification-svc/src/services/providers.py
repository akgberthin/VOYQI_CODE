"""Delivery providers. Each returns (success, delivery_mode, provider_message_id, error_message).

Every provider is honest about whether it actually delivered something or
just logged it: DeliveryMode.CONSOLE means no real credentials were
configured for this environment, and the "notification" only ever
existed as a database row and a log line — never presented as sent.
"""
import logging
import smtplib
import uuid
from email.mime.text import MIMEText

from src.core.config import get_settings
from src.models import DeliveryMode

logger = logging.getLogger(__name__)
settings = get_settings()


async def send_email(*, recipient: str, subject: str, body: str) -> tuple[bool, str, str | None, str | None]:
    if not settings.smtp_host:
        logger.info("CONSOLE EMAIL to=%s subject=%r body=%r", recipient, subject, body)
        return True, DeliveryMode.CONSOLE.value, f"console-{uuid.uuid4()}", None

    msg = MIMEText(body, "html")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_address
    msg["To"] = recipient

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username and settings.smtp_password:
                server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(settings.smtp_from_address, [recipient], msg.as_string())
        return True, DeliveryMode.REAL_PROVIDER.value, None, None
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("SMTP delivery failed for %s: %s", recipient, exc)
        return False, DeliveryMode.REAL_PROVIDER.value, None, str(exc)


async def send_sms(*, recipient: str, body: str) -> tuple[bool, str, str | None, str | None]:
    if not settings.sms_provider_api_key:
        logger.info("CONSOLE SMS to=%s body=%r", recipient, body)
        return True, DeliveryMode.CONSOLE.value, f"console-{uuid.uuid4()}", None

    # A real SMS provider (Twilio-shaped) integration point — no API key
    # is configured in this environment, so this branch is unexercised
    # here, but the shape is real: swap in the actual HTTP call when a
    # provider account exists, nothing else about the engine changes.
    logger.warning("SMS provider configured but no real HTTP integration is wired yet for %s", recipient)
    return False, DeliveryMode.REAL_PROVIDER.value, None, "SMS provider integration not implemented"


async def send_push(*, recipient: str, body: str) -> tuple[bool, str, str | None, str | None]:
    if not settings.push_provider_api_key:
        logger.info("CONSOLE PUSH to=%s body=%r", recipient, body)
        return True, DeliveryMode.CONSOLE.value, f"console-{uuid.uuid4()}", None

    logger.warning("Push provider configured but no real HTTP integration is wired yet for %s", recipient)
    return False, DeliveryMode.REAL_PROVIDER.value, None, "Push provider integration not implemented"
