"""
Social media engine — connected accounts and autonomous publishing.
Scheduling and publishing an approved draft happens with no further
click, same principle as send_engine.py. Publishing itself is a
pluggable call out to each platform's real API (see publish_post's
docstring); this engine owns the scheduling, guardrail check, and
calendar population regardless of which platform adapter is wired in.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CalendarEntry, ContentDraft, ContentStatus, SocialAccount, SocialPost, SocialPostStatus
from src.services.send_engine import DraftNotApprovedError


async def connect_account(db: AsyncSession, tenant_id: str, platform: str, handle: str, access_token_ref: str = "") -> SocialAccount:
    account = SocialAccount(tenant_id=tenant_id, platform=platform, handle=handle, access_token_ref=access_token_ref)
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


async def list_accounts(db: AsyncSession, tenant_id: str) -> list[SocialAccount]:
    result = await db.execute(select(SocialAccount).where(SocialAccount.tenant_id == tenant_id))
    return list(result.scalars().all())


async def disconnect_account(db: AsyncSession, tenant_id: str, account_id: uuid.UUID) -> SocialAccount | None:
    result = await db.execute(select(SocialAccount).where(SocialAccount.id == account_id, SocialAccount.tenant_id == tenant_id))
    account = result.scalar_one_or_none()
    if account is None:
        return None
    account.connected = False
    await db.commit()
    await db.refresh(account)
    return account


async def schedule_post(db: AsyncSession, tenant_id: str, social_account_id: uuid.UUID, content_draft_id: uuid.UUID, scheduled_at: datetime) -> SocialPost:
    """Refuses to schedule a draft that hasn't cleared the guardrail,
    the same structural check as send_engine.schedule_send."""
    draft_result = await db.execute(select(ContentDraft).where(ContentDraft.id == content_draft_id, ContentDraft.tenant_id == tenant_id))
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        raise ValueError(f"content draft {content_draft_id} not found")
    if draft.status != ContentStatus.APPROVED:
        raise DraftNotApprovedError(f"content draft is {draft.status.value}, not approved")

    post = SocialPost(tenant_id=tenant_id, social_account_id=social_account_id, content_draft_id=content_draft_id, scheduled_at=scheduled_at)
    db.add(post)
    draft.status = ContentStatus.SCHEDULED

    await db.flush()
    db.add(CalendarEntry(tenant_id=tenant_id, channel="social", title=f"Post: {draft.body_text[:40]}", scheduled_at=scheduled_at, reference_id=post.id, reference_type="social_post"))

    await db.commit()
    await db.refresh(post)
    return post


async def publish_post(db: AsyncSession, tenant_id: str, post_id: uuid.UUID, platform_post_id: str | None = None) -> SocialPost:
    """Fully autonomous — publishing a scheduled, already-approved
    post requires no further click. The actual outbound call to each
    platform's real API (Instagram Graph API, X API, etc.) is a
    pluggable adapter this function's caller supplies platform_post_id
    from; this engine owns the state transition and calendar/content
    status regardless of which adapter fulfilled the actual publish."""
    result = await db.execute(select(SocialPost).where(SocialPost.id == post_id, SocialPost.tenant_id == tenant_id))
    post = result.scalar_one_or_none()
    if post is None:
        raise ValueError(f"social post {post_id} not found")

    post.status = SocialPostStatus.PUBLISHED
    post.published_at = datetime.now(timezone.utc)
    post.platform_post_id = platform_post_id

    draft_result = await db.execute(select(ContentDraft).where(ContentDraft.id == post.content_draft_id))
    draft = draft_result.scalar_one_or_none()
    if draft:
        draft.status = ContentStatus.SENT

    await db.commit()
    await db.refresh(post)
    return post


async def list_posts(db: AsyncSession, tenant_id: str, status: str | None = None) -> list[SocialPost]:
    query = select(SocialPost).where(SocialPost.tenant_id == tenant_id)
    if status:
        query = query.where(SocialPost.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())
