"""Hands each marketing message to notification-svc, which delivers it
through the tenant's configured provider (or logs it in console mode)."""
import httpx

from src.core.config import get_settings


async def deliver(tenant_id: str, channel: str, recipient: str, body: str, subject: str, source: str, options: dict | None = None) -> dict:
    """Returns {"status": "sent"|"failed"|"skipped_opted_out", "delivery_mode", "error_message"}."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{settings.notification_svc_base_url}/api/v1/notifications/send-direct",
                                     headers={"X-Tenant-Id": tenant_id},
                                     json={"channel": channel, "recipient": recipient, "body": body, "subject": subject or None,
                                           "category": "marketing", "source": source, "options": options or {}})
    except httpx.HTTPError as exc:
        return {"status": "failed", "delivery_mode": None, "error_message": f"notification-svc unreachable ({type(exc).__name__})"}
    if resp.status_code != 201:
        return {"status": "failed", "delivery_mode": None, "error_message": f"notification-svc HTTP {resp.status_code}: {resp.text[:200]}"}
    return resp.json()


async def statuses(tenant_id: str, notification_ids: list[str]) -> list[dict]:
    """What the provider reported for each message (delivered, answered...)."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{settings.notification_svc_base_url}/api/v1/notifications/statuses",
                                 headers={"X-Tenant-Id": tenant_id}, json={"ids": notification_ids})
    resp.raise_for_status()
    return resp.json()
