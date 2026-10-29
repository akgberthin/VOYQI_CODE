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

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
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
    # Channel settings sent with every delivery — see services/channel_options.
    options: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
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

class SegmentMember(Base):
    """A contact in a segment, with whatever channels they can be reached
    on. Imported by the marketing team (or synced from CRM/membership)."""

    __tablename__ = "segment_members"
    # push_token too: contacts reachable only by push have no email or phone.
    __table_args__ = (UniqueConstraint("segment_id", "email", "phone", "push_token", name="uq_segment_member_contact"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    segment_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("audience_segments.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    push_token: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # Prior express written consent to automated (AI voice) calls — TCPA.
    # Voice sends only ever reach members with it.
    consent_voice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # What targeting and personalisation can use: city, class, status,
    # tenure_months, churn_risk, upgrade_ready, gu_balance, hub, … (free-form).
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
    # Of the rest: how many the channel provider refused, and how many
    # only went to the console because no provider is configured.
    recipients_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recipients_console: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Set when a scheduler replica takes the send, so no other replica
    # executes it too.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # none / daily / weekly / 3x_week (Mon, Wed, Fri): after it runs, the
    # next occurrence is scheduled automatically.
    recurrence: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
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
    # "real" when the platform's API accepted it, "console" when no
    # integration is set up (nothing left VOYQI), "external" when another
    # system reported the publish.
    delivery_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    error_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recurrence: Mapped[str] = mapped_column(String(16), nullable=False, default="none")


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


class MediaAsset(Base):
    """An image VOYQI hosts: generated in the AI studio or uploaded. Served
    publicly (social platforms fetch images by URL), so nothing private
    belongs here."""

    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")  # upload / openai
    generation_request_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AutopilotConfig(Base):
    """Social autopilot for a tenant: the Marketing Content Agent plans the
    coming week's posts for the chosen accounts. In "suggest" mode every
    planned post waits for a person; "autopublish" schedules them directly."""

    __tablename__ = "autopilot_configs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="suggest")  # suggest / autopublish
    account_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    posts_per_week: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    post_hour_utc: Mapped[int] = mapped_column(Integer, nullable=False, default=16)
    themes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mobility_class: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    tone: Mapped[str] = mapped_column(String(16), nullable=False, default="premium")
    include_images: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")


class AutopilotSlot(Base):
    """One planned post: when, where, the drafted copy and image."""

    __tablename__ = "autopilot_slots"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    social_account_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("social_accounts.id"), nullable=False)
    planned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    theme: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    content_draft_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    social_post_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    # proposed (waiting for a person) / held (guardrail flagged the copy) /
    # scheduled / rejected
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    decided_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MarketingSettings(Base):
    """Per-tenant channel rules. Voice calls only go out inside the calling
    window (local time in `timezone`); outside it they wait for the next
    window."""

    __tablename__ = "marketing_settings"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Chicago")
    voice_window_start: Mapped[int] = mapped_column(Integer, nullable=False, default=9)
    voice_window_end: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    # SMS quiet hours: marketing texts only go out between these (local) hours.
    sms_window_start: Mapped[int] = mapped_column(Integer, nullable=False, default=9)
    sms_window_end: Mapped[int] = mapped_column(Integer, nullable=False, default=21)
    # Emailed performance report: off / weekly / monthly, to these people.
    report_frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="off")
    report_recipients: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    report_last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")


class Journey(Base):
    """An automation flow: when trigger_event happens for a contact (or they
    are enrolled by hand), each step goes out after its delay."""

    __tablename__ = "journeys"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    trigger_event: Mapped[str] = mapped_column(String(128), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="paused")  # active / paused
    # [{"delay_hours": 0, "channel": "email", "subject": "...", "body": "..."}]
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class JourneyEnrollment(Base):
    __tablename__ = "journey_enrollments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    journey_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("journeys.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    push_token: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    consent_voice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active / completed / stopped
    history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# Growth / SEO engine tables live with the engine; importing them here
# registers them on Base.metadata for Alembic and create_all.
from src.seo import models as seo_models  # noqa: E402,F401


class MessageRecipient(Base):
    """One message delivered to one contact by a send — what opens and
    clicks are recorded against (tracking pixel, tracked links), and which
    A/B arm the contact got."""

    __tablename__ = "message_recipients"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    send_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    content_draft_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    ab_test_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    variant: Mapped[str] = mapped_column(String(1), nullable=False, default="")
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    contact: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
