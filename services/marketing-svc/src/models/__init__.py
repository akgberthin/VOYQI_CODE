"""
marketing-svc data model — voice, SMS, email, social, and push
marketing, a content library, a shared calendar across every channel,
audience segmentation, A/B testing, analytics, and pluggable media
generation.

Governing principle, same as everywhere else on this platform: full
automation by default. There is exactly one hard, structural gate in
this entire service: a contact on the suppression list can never
receive a send, on any channel, no override. TCPA (SMS/voice) and
CAN-SPAM (email) make honoring an opt-out a legal requirement, not a
judgment call — the same shape as OFAC elsewhere on this platform.

AI-drafted content is not held for mandatory human review by default.
Instead, every draft passes through a deterministic guardrail (brand-
voice keyword check, channel length limits) before it can be
auto-approved and auto-scheduled — the guardrail is the AI-native
safety mechanism, not a human click. A draft that fails the guardrail
is held for a human to look at; one that passes ships on its own.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, GUID


# ══════════════════════════════════════════════════════════════════
# Channels — shared across campaigns, content, calendar, and analytics
# ══════════════════════════════════════════════════════════════════

class Channel(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"
    VOICE = "voice"
    SOCIAL = "social"
    PUSH = "push"


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    objective: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[CampaignStatus] = mapped_column(Enum(CampaignStatus), nullable=False, default=CampaignStatus.DRAFT)
    audience_segment_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("audience_segments.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Content — drafts (per campaign) and the reusable library
# ══════════════════════════════════════════════════════════════════

class ContentStatus(str, enum.Enum):
    DRAFT = "draft"
    HELD_FOR_REVIEW = "held_for_review"  # failed the deterministic guardrail
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    SENT = "sent"
    REJECTED = "rejected"


class ContentDraft(Base):
    """status progresses DRAFT -> (guardrail check) -> APPROVED or
    HELD_FOR_REVIEW, autonomously, the instant a draft is created —
    see content_engine.evaluate_guardrail. Only HELD_FOR_REVIEW drafts
    ever wait on a human; everything else moves on its own."""

    __tablename__ = "content_drafts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("campaigns.id"), nullable=False)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    subject_line: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    media_asset_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[ContentStatus] = mapped_column(Enum(ContentStatus), nullable=False, default=ContentStatus.DRAFT)
    guardrail_findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ContentLibraryItem(Base):
    """Reusable, channel-tagged content blocks a campaign can draw
    from — the 'content library' — distinct from ContentDraft, which
    is always tied to one specific campaign send."""

    __tablename__ = "content_library_items"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Note(Base):
    """Freeform campaign notes — distinct from ContentDraft, which is
    customer-facing; a Note is internal-only, never sent anywhere."""

    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("campaigns.id"), nullable=True)
    author: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Audience segmentation
# ══════════════════════════════════════════════════════════════════

class AudienceSegment(Base):
    __tablename__ = "audience_segments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    filter_criteria: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Suppression list — the ONE hard, structural gate in this service.
# ══════════════════════════════════════════════════════════════════

class SuppressionReason(str, enum.Enum):
    OPT_OUT = "opt_out"
    BOUNCE = "bounce"
    COMPLAINT = "complaint"


class SuppressionListEntry(Base):
    """A contact_identifier + channel pair on this table can never
    receive a send on that channel — checked unconditionally by
    send_engine before every single send, with no override function
    anywhere in this codebase. Removing an entry (e.g. a contact who
    re-subscribes through a proper opt-in flow) is the only way off
    this list, and that's a separate, deliberate action, not
    something a send can bypass."""

    __tablename__ = "suppression_list_entries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    contact_identifier: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[SuppressionReason] = mapped_column(Enum(SuppressionReason), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Scheduled sends (email / SMS / voice / push) and the shared calendar
# ══════════════════════════════════════════════════════════════════

class SendStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    SENT = "sent"
    FAILED = "failed"
    BLOCKED_SUPPRESSION = "blocked_suppression"


class ScheduledSend(Base):
    __tablename__ = "scheduled_sends"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("campaigns.id"), nullable=False)
    content_draft_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("content_drafts.id"), nullable=False)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[SendStatus] = mapped_column(Enum(SendStatus), nullable=False, default=SendStatus.SCHEDULED)
    recipients_targeted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recipients_suppressed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CalendarEntry(Base):
    """One row per scheduled item across every channel — the single
    calendar view spanning email/SMS/voice/social/push, autonomously
    populated whenever something is scheduled anywhere in this
    service, not a separate manual entry a caller has to remember to
    make."""

    __tablename__ = "calendar_entries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False)  # ScheduledSend.id or SocialPost.id
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "scheduled_send" | "social_post"


# ══════════════════════════════════════════════════════════════════
# Social media — connected accounts and posts
# ══════════════════════════════════════════════════════════════════

class SocialPlatform(str, enum.Enum):
    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"
    TIKTOK = "tiktok"


class SocialAccount(Base):
    __tablename__ = "social_accounts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    platform: Mapped[SocialPlatform] = mapped_column(Enum(SocialPlatform), nullable=False)
    handle: Mapped[str] = mapped_column(String(128), nullable=False)
    connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    access_token_ref: Mapped[str] = mapped_column(String(256), nullable=False, default="")


class SocialPostStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"
    BLOCKED_REVIEW = "blocked_review"


class SocialPost(Base):
    __tablename__ = "social_posts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("social_accounts.id"), nullable=False)
    content_draft_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("content_drafts.id"), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[SocialPostStatus] = mapped_column(Enum(SocialPostStatus), nullable=False, default=SocialPostStatus.SCHEDULED)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    platform_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


# ══════════════════════════════════════════════════════════════════
# A/B testing — autonomous conclusion once statistically significant
# ══════════════════════════════════════════════════════════════════

class ABTestStatus(str, enum.Enum):
    RUNNING = "running"
    CONCLUDED = "concluded"
    INSUFFICIENT_DATA = "insufficient_data"


class ABTest(Base):
    __tablename__ = "ab_tests"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("campaigns.id"), nullable=False)
    variant_a_draft_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("content_drafts.id"), nullable=False)
    variant_b_draft_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("content_drafts.id"), nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False, default="click_rate")
    variant_a_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    variant_a_converted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    variant_b_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    variant_b_converted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ABTestStatus] = mapped_column(Enum(ABTestStatus), nullable=False, default=ABTestStatus.RUNNING)
    winner_draft_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    concluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Analytics — real counters, no AI reasoning belongs on a metrics row
# ══════════════════════════════════════════════════════════════════

class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("campaigns.id"), nullable=False)
    channel: Mapped[Channel] = mapped_column(Enum(Channel), nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversion_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revenue_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Media generation — pluggable adapter interface. This service does
# not embed any one image/video-generation vendor; it defines the
# request/result contract a real MCP-connected provider fulfills
# (see media_generation_engine.py's docstring for the integration
# point), so the "best" model can be swapped without a schema change.
# ══════════════════════════════════════════════════════════════════

class MediaType(str, enum.Enum):
    IMAGE = "image"
    VIDEO = "video"


class MediaGenerationStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class MediaGenerationRequest(Base):
    __tablename__ = "media_generation_requests"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="unconfigured")
    status: Mapped[MediaGenerationStatus] = mapped_column(Enum(MediaGenerationStatus), nullable=False, default=MediaGenerationStatus.PENDING)
    result_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Landing-page lead capture — the raw, channel-side record. Publishing
# lead.captured hands it to the Revenue Agent, which creates the
# unified crm-svc Lead and scores it (CRM-002).
# ══════════════════════════════════════════════════════════════════

class LeadCapture(Base):
    __tablename__ = "lead_captures"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("campaigns.id"), nullable=True)
    landing_page: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    consent_marketing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
