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

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CalendarEntry, ContentDraft, ContentStatus, SocialAccount, SocialPost, SocialPostStatus
from src.core import integrations
from src.services import channel_policy, media_engine, social_adapters
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


async def schedule_post(db: AsyncSession, tenant_id: str, social_account_id: uuid.UUID, content_draft_id: uuid.UUID, scheduled_at: datetime,
                        media_asset_id: uuid.UUID | None = None, recurrence: str = "none") -> SocialPost:
    """Refuses to schedule a draft that hasn't cleared the guardrail,
    the same structural check as send_engine.schedule_send."""
    draft_result = await db.execute(select(ContentDraft).where(ContentDraft.id == content_draft_id, ContentDraft.tenant_id == tenant_id))
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        raise ValueError(f"content draft {content_draft_id} not found")
    if draft.status != ContentStatus.APPROVED:
        raise DraftNotApprovedError(f"content draft is {draft.status.value}, not approved")

    if recurrence not in channel_policy.RECURRENCES:
        raise ValueError(f"recurrence must be one of {', '.join(channel_policy.RECURRENCES)}")
    post = SocialPost(tenant_id=tenant_id, social_account_id=social_account_id, content_draft_id=content_draft_id, scheduled_at=scheduled_at,
                      media_asset_id=media_asset_id, recurrence=recurrence)
    db.add(post)
    draft.status = ContentStatus.SCHEDULED

    await db.flush()
    db.add(CalendarEntry(tenant_id=tenant_id, channel="social", title=f"Post: {draft.body_text[:40]}", scheduled_at=scheduled_at, reference_id=post.id, reference_type="social_post"))

    await db.commit()
    await db.refresh(post)
    return post


class AlreadyPublishedError(Exception):
    pass


async def claim_post(db: AsyncSession, tenant_id: str, post_id: uuid.UUID) -> bool:
    result = await db.execute(
        update(SocialPost).where(SocialPost.id == post_id, SocialPost.tenant_id == tenant_id,
                                 SocialPost.status == SocialPostStatus.SCHEDULED, SocialPost.claimed_at.is_(None))
        .values(claimed_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return result.rowcount == 1


async def publish_post(db: AsyncSession, tenant_id: str, post_id: uuid.UUID, platform_post_id: str | None = None, *, claimed: bool = False) -> SocialPost:
    """Publishes a scheduled, approved post through the platform's API with
    the tenant's credentials (Integrations). Without credentials it is
    published in console mode — recorded, never presented as live. When
    another system already published it, pass its platform_post_id."""
    post = (await db.execute(select(SocialPost).where(SocialPost.id == post_id, SocialPost.tenant_id == tenant_id))).scalar_one_or_none()
    if post is None:
        raise ValueError(f"social post {post_id} not found")
    if not claimed and not await claim_post(db, tenant_id, post_id):
        await db.refresh(post)
        raise AlreadyPublishedError(f"post is already {post.status.value}")
    await db.refresh(post)
    account = (await db.execute(select(SocialAccount).where(SocialAccount.id == post.social_account_id))).scalar_one()
    draft = (await db.execute(select(ContentDraft).where(ContentDraft.id == post.content_draft_id))).scalar_one_or_none()
    platform = account.platform.value if hasattr(account.platform, "value") else str(account.platform)

    if platform_post_id:
        post.delivery_mode, post.platform_post_id = "external", platform_post_id
    else:
        config = await integrations.get(tenant_id, social_adapters.INTEGRATION.get(platform, ""))
        if not account.connected:
            post.status, post.error_detail = SocialPostStatus.FAILED, f"{platform} account {account.handle} is disconnected"
        elif not config:
            post.delivery_mode, post.platform_post_id = "console", f"console-{uuid.uuid4().hex[:12]}"
        else:
            image_url = media_engine.public_url(post.media_asset_id or _first_asset(draft))
            try:
                post.platform_post_id = await social_adapters.publish(platform, config, draft.body_text if draft else "", image_url)
                post.delivery_mode = "real"
            except social_adapters.PublishError as exc:
                post.status, post.error_detail = SocialPostStatus.FAILED, str(exc)[:2000]

    if post.status != SocialPostStatus.FAILED:
        post.status = SocialPostStatus.PUBLISHED
        post.published_at = datetime.now(timezone.utc)
        if draft:
            draft.status = ContentStatus.SENT
    nxt = channel_policy.next_occurrence(post.scheduled_at, post.recurrence)
    if nxt is not None:
        nxt = nxt if nxt.tzinfo else nxt.replace(tzinfo=timezone.utc)
        while nxt <= datetime.now(timezone.utc):
            nxt = channel_policy.next_occurrence(nxt, post.recurrence)
        follow = SocialPost(tenant_id=tenant_id, social_account_id=post.social_account_id, content_draft_id=post.content_draft_id,
                            scheduled_at=nxt, media_asset_id=post.media_asset_id, recurrence=post.recurrence)
        db.add(follow)
        await db.flush()
        db.add(CalendarEntry(tenant_id=tenant_id, channel="social", title=f"Post: {(draft.body_text if draft else '')[:40]} (recurring)", scheduled_at=nxt, reference_id=follow.id, reference_type="social_post"))
    await db.commit()
    await db.refresh(post)
    return post


def _first_asset(draft: ContentDraft | None):
    ids = (draft.media_asset_ids or []) if draft else []
    try:
        return uuid.UUID(str(ids[0])) if ids else None
    except ValueError:
        return None


async def due_posts(db: AsyncSession) -> list[SocialPost]:
    now = datetime.now(timezone.utc)
    result = await db.execute(select(SocialPost).where(SocialPost.status == SocialPostStatus.SCHEDULED, SocialPost.claimed_at.is_(None), SocialPost.scheduled_at <= now))
    return list(result.scalars().all())


async def list_posts(db: AsyncSession, tenant_id: str, status: str | None = None) -> list[SocialPost]:
    query = select(SocialPost).where(SocialPost.tenant_id == tenant_id)
    if status:
        query = query.where(SocialPost.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())
