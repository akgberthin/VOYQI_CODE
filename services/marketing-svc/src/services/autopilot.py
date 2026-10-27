"""Social autopilot: the Marketing Content Agent plans the coming week's
posts for each chosen account — copy (through the guardrail like any
draft) and, with an OpenAI integration, an image — at evenly spaced
slots. In "suggest" mode each planned post waits for a person; in
"autopublish" mode clean ones are scheduled straight away. A post the
guardrail flags is always held for review, in either mode."""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import integrations
from src.core.kafka_events import publish_event
from src.models import AutopilotConfig, AutopilotSlot, Campaign, CampaignStatus, ContentStatus, SocialAccount
from src.services import ai_drafting, campaign_engine, content_engine, media_engine, social_engine

logger = logging.getLogger("marketing-svc.autopilot")

MODES = ("suggest", "autopublish")
HORIZON_DAYS = 7
REPLAN_AFTER = timedelta(hours=12)
SLOT_TOLERANCE = timedelta(hours=3)
FIELDS = ("enabled", "mode", "account_ids", "posts_per_week", "post_hour_utc", "themes", "mobility_class", "tone", "include_images")


class AutopilotError(Exception):
    pass


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; everything here is UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


async def get_config(db: AsyncSession, tenant_id: str) -> AutopilotConfig:
    cfg = (await db.execute(select(AutopilotConfig).where(AutopilotConfig.tenant_id == tenant_id))).scalar_one_or_none()
    if cfg is None:
        cfg = AutopilotConfig(tenant_id=tenant_id, enabled=False, mode="suggest", account_ids=[], posts_per_week=3,
                              post_hour_utc=16, themes="", mobility_class="", tone="premium", include_images=True, updated_by="")
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return cfg


async def save_config(db: AsyncSession, tenant_id: str, values: dict, actor: str) -> AutopilotConfig:
    cfg = await get_config(db, tenant_id)
    if values.get("mode", cfg.mode) not in MODES:
        raise AutopilotError(f"mode must be one of {', '.join(MODES)}")
    if not 1 <= int(values.get("posts_per_week", cfg.posts_per_week)) <= 21:
        raise AutopilotError("posts_per_week must be between 1 and 21")
    if not 0 <= int(values.get("post_hour_utc", cfg.post_hour_utc)) <= 23:
        raise AutopilotError("post_hour_utc must be 0-23")
    for key in FIELDS:
        if key in values:
            setattr(cfg, key, [str(a) for a in values[key]] if key == "account_ids" else values[key])
    cfg.updated_by = actor
    cfg.last_planned_at = None  # replan with the new settings
    await db.commit()
    await db.refresh(cfg)
    return cfg


def slot_times(cfg: AutopilotConfig, now: datetime) -> list[datetime]:
    """posts_per_week slots spread evenly over the next week, at the hour."""
    start = (now + timedelta(days=1)).replace(hour=cfg.post_hour_utc, minute=0, second=0, microsecond=0)
    n = cfg.posts_per_week
    return [start + timedelta(days=(i * HORIZON_DAYS) // n) for i in range(n)]


async def _campaign_for(db: AsyncSession, tenant_id: str, platform: str) -> Campaign:
    name = f"Autopilot — {platform.title()}"
    found = (await db.execute(select(Campaign).where(Campaign.tenant_id == tenant_id, Campaign.name == name))).scalars().first()
    if found:
        return found
    campaign = await campaign_engine.create_campaign(db, tenant_id, name, "social", "Always-on social presence planned by the Marketing Content Agent", "ai:autopilot")
    await campaign_engine.set_campaign_status(db, tenant_id, campaign.id, CampaignStatus.ACTIVE.value)
    return campaign


async def plan(db: AsyncSession, tenant_id: str, now: datetime | None = None) -> int:
    """Fills empty slots in the coming week. Returns how many were planned."""
    now = now or datetime.now(timezone.utc)
    cfg = await get_config(db, tenant_id)
    if not cfg.enabled:
        return 0
    accounts = [a for a in (await db.execute(select(SocialAccount).where(SocialAccount.tenant_id == tenant_id, SocialAccount.connected.is_(True)))).scalars()
                if not cfg.account_ids or str(a.id) in cfg.account_ids]
    themes = [t.strip() for t in cfg.themes.splitlines() if t.strip()] or ["Portable Mobility: every class, every hub, no ownership"]
    openai = await integrations.get(tenant_id, "openai") if cfg.include_images else None
    existing = list((await db.execute(select(AutopilotSlot).where(AutopilotSlot.tenant_id == tenant_id, AutopilotSlot.status != "rejected", AutopilotSlot.planned_at > now))).scalars())
    planned = 0
    for account in accounts:
        platform = account.platform.value if hasattr(account.platform, "value") else str(account.platform)
        for index, at in enumerate(slot_times(cfg, now)):
            if any(s.social_account_id == account.id and abs(_aware(s.planned_at) - at) < SLOT_TOLERANCE for s in existing):
                continue
            theme = themes[(index + planned) % len(themes)]
            out = await ai_drafting.compose(tenant_id, {"channel": "social", "mobility_class": cfg.mobility_class, "objective": "acquisition", "tones": [cfg.tone], "context": theme})
            if not out.get("variants"):
                logger.info("autopilot for %s stopped: agent %s", tenant_id, out.get("skipped_reason"))
                cfg.last_planned_at = now
                await db.commit()
                return planned
            campaign = await _campaign_for(db, tenant_id, platform)
            draft = await content_engine.create_content_draft(db, tenant_id, campaign.id, "social", out["variants"][0]["body"], "ai:autopilot")
            asset_id = None
            if openai:
                try:
                    data = await media_engine.openai_image(openai, f"Premium automotive photograph for a social post: {theme}. No text in the image.")
                    asset_id = (await media_engine.store(db, tenant_id, data, "image/png", theme, "openai", "ai:autopilot")).id
                except Exception as exc:  # the post still goes ahead without an image
                    logger.warning("autopilot image for %s failed: %s", tenant_id, exc)
            slot = AutopilotSlot(tenant_id=tenant_id, social_account_id=account.id, planned_at=at, theme=theme,
                                 content_draft_id=draft.id, media_asset_id=asset_id,
                                 status="held" if draft.status == ContentStatus.HELD_FOR_REVIEW else "proposed")
            db.add(slot)
            await db.commit()
            if slot.status == "proposed" and cfg.mode == "autopublish":
                await _schedule(db, tenant_id, slot, "ai:autopilot")
            existing.append(slot)
            planned += 1
    cfg.last_planned_at = now
    await db.commit()
    return planned


async def _schedule(db: AsyncSession, tenant_id: str, slot: AutopilotSlot, actor: str) -> AutopilotSlot:
    post = await social_engine.schedule_post(db, tenant_id, slot.social_account_id, slot.content_draft_id, slot.planned_at, slot.media_asset_id)
    slot.social_post_id, slot.status, slot.decided_by = post.id, "scheduled", actor
    await db.commit()
    await db.refresh(slot)
    return slot


async def _slot(db: AsyncSession, tenant_id: str, slot_id: uuid.UUID) -> AutopilotSlot:
    slot = (await db.execute(select(AutopilotSlot).where(AutopilotSlot.id == slot_id, AutopilotSlot.tenant_id == tenant_id))).scalar_one_or_none()
    if slot is None:
        raise AutopilotError("slot not found")
    return slot


async def approve(db: AsyncSession, tenant_id: str, slot_id: uuid.UUID, actor: str) -> AutopilotSlot:
    slot = await _slot(db, tenant_id, slot_id)
    if slot.status not in ("proposed", "held"):
        raise AutopilotError(f"slot is already {slot.status}")
    draft = await content_engine.get_content_draft(db, tenant_id, slot.content_draft_id)
    if draft.status == ContentStatus.HELD_FOR_REVIEW:
        # A person approving the slot is the human review the guardrail
        # asked for — recorded the same way as approving it in Content review.
        await content_engine.human_approve_held_draft(db, tenant_id, draft.id, actor)
        await publish_event("marketing.content", "content_draft.approved_with_violations", tenant_id, {
            "draft_id": str(draft.id), "violation_count": len(draft.guardrail_findings or []),
            "findings": list(draft.guardrail_findings or []), "reviewed_by": actor,
        })
    return await _schedule(db, tenant_id, slot, actor)


async def reject(db: AsyncSession, tenant_id: str, slot_id: uuid.UUID, actor: str) -> AutopilotSlot:
    slot = await _slot(db, tenant_id, slot_id)
    if slot.status not in ("proposed", "held"):
        raise AutopilotError(f"slot is already {slot.status}")
    await content_engine.reject_draft(db, tenant_id, slot.content_draft_id, actor)
    slot.status, slot.decided_by = "rejected", actor
    await db.commit()
    await db.refresh(slot)
    return slot


async def list_slots(db: AsyncSession, tenant_id: str) -> list[AutopilotSlot]:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    result = await db.execute(select(AutopilotSlot).where(AutopilotSlot.tenant_id == tenant_id, AutopilotSlot.planned_at > since).order_by(AutopilotSlot.planned_at))
    return list(result.scalars().all())


async def due_configs(db: AsyncSession, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    rows = (await db.execute(select(AutopilotConfig).where(AutopilotConfig.enabled.is_(True)))).scalars()
    return [c.tenant_id for c in rows if c.last_planned_at is None or now - _aware(c.last_planned_at) >= REPLAN_AFTER]
