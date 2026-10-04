"""Default templates, seeded on startup if not already present. Callers
across the platform (auth-svc's password reset, subscription-svc's
billing confirmations, HRM's onboarding notices) depend on these keys
existing — this is the other half of closing the "channels record
intent only" gap noted throughout the platform's build history.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import NotificationCategory, NotificationTemplate

DEFAULT_TEMPLATES = [
    {
        "key": "password_reset",
        "channel": "email",
        "category": NotificationCategory.SECURITY.value,
        "subject_template": "Reset your VOYQI password",
        "body_template": (
            "<p>We received a request to reset your VOYQI password.</p>"
            "<p>Your reset code: <strong>{{ reset_token }}</strong></p>"
            "<p>This code expires in 30 minutes. If you didn't request this, you can ignore this email.</p>"
        ),
    },
    {
        "key": "welcome_member",
        "channel": "email",
        "category": NotificationCategory.TRANSACTIONAL.value,
        "subject_template": "Welcome to VOYQI, {{ member_name }}",
        "body_template": "<p>Welcome, {{ member_name }}! Your {{ plan_class }} membership is now active.</p>",
    },
    {
        "key": "deposit_capture_notice",
        "channel": "email",
        "category": NotificationCategory.TRANSACTIONAL.value,
        "subject_template": "Deposit capture on your VOYQI account",
        "body_template": "<p>${{ amount }} of your ${{ hold_amount }} deposit hold has been captured. Reference: {{ reference_id }}.</p>",
    },
    {
        "key": "upgrade_case_offered",
        "channel": "email",
        "category": NotificationCategory.MARKETING.value,
        "subject_template": "An upgrade is available on your account",
        "body_template": "<p>Based on your recent usage, you're eligible to upgrade from {{ from_class }} to {{ to_class }}.</p>",
    },
    {
        "key": "security_alert",
        "channel": "email",
        "category": NotificationCategory.SECURITY.value,
        "subject_template": "Security alert on your VOYQI account",
        "body_template": "<p>{{ alert_message }}</p><p>If this wasn't you, reset your password immediately.</p>",
    },
]


async def seed_default_templates(db: AsyncSession, *, tenant_id: str) -> int:
    """Idempotent — only inserts templates that don't already exist for
    this tenant+key+channel. Returns the count actually inserted."""
    inserted = 0
    for spec in DEFAULT_TEMPLATES:
        result = await db.execute(
            select(NotificationTemplate).where(
                NotificationTemplate.tenant_id == tenant_id,
                NotificationTemplate.key == spec["key"],
                NotificationTemplate.channel == spec["channel"],
            )
        )
        if result.scalar_one_or_none() is not None:
            continue
        db.add(NotificationTemplate(tenant_id=tenant_id, **spec))
        inserted += 1
    await db.flush()
    return inserted
