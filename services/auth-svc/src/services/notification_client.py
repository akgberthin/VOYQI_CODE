"""Thin client for calling notification-svc. Failures are logged, never
raised — a down notification-svc must never block a password reset flow
or any other core auth operation. This mirrors the same
graceful-degradation posture used for every other cross-service call
across the platform (e.g. ai-orchestration-svc calls from other
services)."""
import logging

import httpx

from src.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def send_password_reset_email(*, tenant_id: str, email: str, raw_token: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{settings.notification_svc_base_url}/api/v1/notifications/send",
                headers={"X-Tenant-Id": tenant_id},
                json={
                    "channel": "email",
                    "template": "password_reset",
                    "recipient": email,
                    "variables": {"reset_token": raw_token},
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("notification-svc unreachable for password reset email to %s: %s", email, exc)
