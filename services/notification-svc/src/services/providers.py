"""Delivery providers. Each returns (success, delivery_mode, provider_message_id, error_message).

Credentials come from the tenant's integrations (super admin console),
then env settings. With neither, a provider is honest about it:
DeliveryMode.CONSOLE means the message was rendered and logged, never
presented as delivered to a real person.
"""
import asyncio
import json
import logging
import smtplib
import time
import uuid
from email.mime.text import MIMEText
from email.utils import formataddr
from xml.sax.saxutils import escape

import httpx

from src.core import integrations
from src.core.config import get_settings
from src.models import DeliveryMode

logger = logging.getLogger(__name__)
TIMEOUT = 20.0
Result = tuple[bool, str, str | None, str | None]


def _console(kind: str, recipient: str, body: str) -> Result:
    logger.info("CONSOLE %s to=%s body=%r", kind, recipient, body)
    return True, DeliveryMode.CONSOLE.value, f"console-{uuid.uuid4()}", None


def _failed(error: str) -> Result:
    return False, DeliveryMode.REAL_PROVIDER.value, None, error[:500]


def _header(value: str | None) -> str:
    """A header value someone typed — never allowed to start a new header."""
    return " ".join(str(value or "").split())


def with_preheader(body: str, preview_text: str | None) -> str:
    """The inbox preview line: a hidden first element of an HTML email.
    Plain-text mail has no such element, so it's left as written."""
    text = _header(preview_text)
    if not text or "<" not in body:
        return body
    return f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{escape(text)}</div>{body}'


async def send_email(*, recipient: str, subject: str, body: str, tenant_id: str | None = None,
                     from_name: str | None = None, reply_to: str | None = None, preview_text: str | None = None) -> Result:
    settings = get_settings()
    c = await integrations.get(tenant_id, "smtp") or (
        {"host": settings.smtp_host, "port": settings.smtp_port, "username": settings.smtp_username, "password": settings.smtp_password,
         "from_address": settings.smtp_from_address, "use_tls": str(settings.smtp_use_tls).lower()} if settings.smtp_host else None
    )
    body = with_preheader(body, preview_text)
    if not c:
        return _console("EMAIL", recipient, f"{subject} | {body}")
    msg = MIMEText(body, "html" if "<" in body else "plain")
    msg["Subject"], msg["To"] = subject, recipient
    msg["From"] = formataddr((_header(from_name), c["from_address"])) if _header(from_name) else c["from_address"]
    if _header(reply_to):
        msg["Reply-To"] = _header(reply_to)

    def deliver():
        with smtplib.SMTP(c["host"], int(c.get("port") or 587), timeout=TIMEOUT) as server:
            if str(c.get("use_tls", "true")).lower() == "true":
                server.starttls()
            if c.get("username") and c.get("password"):
                server.login(c["username"], c["password"])
            server.sendmail(c["from_address"], [recipient], msg.as_string())

    try:
        await asyncio.to_thread(deliver)
        return True, DeliveryMode.REAL_PROVIDER.value, None, None
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("SMTP delivery failed for %s: %s", recipient, exc)
        return _failed(str(exc))


async def _twilio(c: dict, resource: str, data: dict) -> Result:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(f"https://api.twilio.com/2010-04-01/Accounts/{c['account_sid']}/{resource}.json",
                                 auth=(c["account_sid"], c["auth_token"]), data=data)
    if resp.status_code in (200, 201):
        return True, DeliveryMode.REAL_PROVIDER.value, resp.json().get("sid"), None
    try:
        message = resp.json().get("message")
    except ValueError:
        message = resp.text[:200]
    return _failed(f"Twilio HTTP {resp.status_code}: {message}")


async def send_sms(*, recipient: str, body: str, tenant_id: str | None = None, sender_id: str | None = None, media_url: str | None = None) -> Result:
    """`sender_id` replaces the integration's number as the sender (an
    alphanumeric ID only works where carriers allow it — not the US);
    `media_url` makes it an MMS with that image."""
    c = await integrations.get(tenant_id, "twilio")
    if not c:
        return _console("SMS", recipient, body + (f" [media {media_url}]" if media_url else ""))
    data = {"To": recipient, "From": _header(sender_id) or c["from_number"], "Body": body}
    if media_url and str(media_url).startswith("https://"):
        data["MediaUrl"] = media_url
    try:
        return await _twilio(c, "Messages", data)
    except httpx.HTTPError as exc:
        return _failed(f"Twilio unreachable: {type(exc).__name__}")


async def send_voice(*, recipient: str, body: str, tenant_id: str | None = None, voice: str | None = None, language: str | None = None) -> Result:
    """An outbound call that reads the script aloud. It opens by saying
    it's an automated call (required for AI voice under TCPA). `voice`
    (e.g. Polly.Joanna) and `language` (e.g. en-US) override the
    integration's default for this call."""
    c = await integrations.get(tenant_id, "twilio")
    if not c:
        return _console("VOICE", recipient, body)
    attrs = f'voice="{escape(_header(voice) or c.get("voice") or "Polly.Joanna", {chr(34): "&quot;"})}"'
    if _header(language):
        attrs += f' language="{escape(_header(language), {chr(34): "&quot;"})}"'
    twiml = (f'<Response><Say {attrs}>This is an automated call from VOYQI.</Say>'
             f'<Pause length="1"/><Say {attrs}>{escape(body)}</Say></Response>')
    try:
        return await _twilio(c, "Calls", {"To": recipient, "From": c["from_number"], "Twiml": twiml})
    except httpx.HTTPError as exc:
        return _failed(f"Twilio unreachable: {type(exc).__name__}")


_fcm_tokens: dict[str, tuple[float, str, str]] = {}


async def _fcm_token(service_account_json: str) -> tuple[str, str]:
    import jwt  # PyJWT with the cryptography backend

    cached = _fcm_tokens.get(service_account_json)
    if cached and cached[0] > time.time() + 60:
        return cached[1], cached[2]
    account = json.loads(service_account_json)
    now = int(time.time())
    token_uri = account.get("token_uri", "https://oauth2.googleapis.com/token")
    assertion = jwt.encode({"iss": account["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
                            "aud": token_uri, "iat": now, "exp": now + 3600}, account["private_key"], algorithm="RS256")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _fcm_tokens[service_account_json] = (now + 3300, token, account["project_id"])
    return token, account["project_id"]


async def send_push(*, recipient: str, body: str, subject: str = "", tenant_id: str | None = None, data: dict | None = None) -> Result:
    """recipient is the device's FCM registration token; `data` (string
    values, e.g. deep_link) reaches the app with the notification."""
    c = await integrations.get(tenant_id, "fcm")
    if not c:
        return _console("PUSH", recipient, f"{subject} | {body}" + (f" | {data}" if data else ""))
    message = {"token": recipient, "notification": {"title": subject or "VOYQI", "body": body}}
    if data:
        message["data"] = {str(k): str(v) for k, v in data.items()}
    try:
        token, project = await _fcm_token(c["service_account_json"])
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(f"https://fcm.googleapis.com/v1/projects/{project}/messages:send",
                                     headers={"Authorization": f"Bearer {token}"}, json={"message": message})
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return _failed(f"FCM: {type(exc).__name__}: {exc}")
    if resp.status_code == 200:
        return True, DeliveryMode.REAL_PROVIDER.value, resp.json().get("name"), None
    return _failed(f"FCM HTTP {resp.status_code}: {resp.text[:200]}")
