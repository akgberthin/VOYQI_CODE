"""
rbac-svc data model — backend for the Super Admin Panel
(admin.voyqi.com/super-admin): the global tenant directory, RBAC roles
and permission overrides, ABAC policy authoring, module/panel
distribution, and the platform-wide audit log.

Governing principle, same as everywhere else on this platform: full
automation by default, with human-only gates reserved for genuine
security matters. Two distinct gates exist here, and they are NOT the
same shape:

1. PermissionOverride risk monitoring is threshold-based (see
   compliance_audit_agent.py in ai-orchestration-svc): a clearly
   unwarranted override is auto-revoked below the agent's risk
   threshold; anything at or above it queues for human review. This
   is deliberately NOT full automation — an identity/access anomaly
   is exactly the kind of ambiguous case a threshold-and-review model
   fits better than "always act" or "never act."

2. ABACPolicy application is a hard Tier-3 gate with NO threshold at
   all: simulation re-runs automatically on every edit (Tier 1), but
   applying a policy grant requires a named accountable_role and
   cannot be reached by any risk score, no matter how low. Broad
   access-control grants are the one place on this platform where
   "auto-execute if the risk looks low" is never the right model.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, GUID


# ══════════════════════════════════════════════════════════════════
# SAP-001 — Global Tenant Directory
# ══════════════════════════════════════════════════════════════════

class TenantStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Tenant(Base):
    """One row per tenant across the whole platform — Auto Group,
    Fleet, Mobility, Rental, Real Estate, Capital, and any future
    subsidiary. This table is itself the global directory; there is
    no separate 'directory' table duplicating it."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    subsidiary: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TenantStatus] = mapped_column(Enum(TenantStatus), nullable=False, default=TenantStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# SAP-006 — Platform Users
# ══════════════════════════════════════════════════════════════════

class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class PlatformUser(Base):
    __tablename__ = "platform_users"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), nullable=False, default=UserStatus.ACTIVE)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# SAP-002 — RBAC: roles, permissions, role assignments, and the
# permission-override risk-monitoring loop
# ══════════════════════════════════════════════════════════════════

class Role(Base):
    """A system role is seeded at startup and cannot be deleted
    through this API — only permission_keys and wildcard_permissions
    are editable, and only by a call that logs an AuditLogEntry."""

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_system_role: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    permission_keys: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    wildcard_permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class RoleAssignment(Base):
    """Assigning a role to a user is fully autonomous-eligible — it's
    a routine grant of an already-defined, already-reviewed role, not
    a new access-control policy being authored. The distinction that
    matters is ABACPolicy below, which changes what a role itself
    grants, not who holds one."""

    __tablename__ = "role_assignments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("platform_users.id"), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("roles.id"), nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(128), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class OverrideEffect(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


class PermissionOverride(Base):
    """A one-off grant or denial outside a user's normal role. DENY
    always wins over any ALLOW from a role or another override — see
    rbac_engine.resolve_effective_permissions, which is the one place
    that rule is actually enforced, not just documented.

    risk_flagged and revoked* are written by the Compliance/Audit
    Agent's threshold-based monitoring (see the module docstring) —
    this is the one RBAC action on this platform that is NOT simply
    'autonomous by default,' because an identity/access anomaly is
    exactly the ambiguous case a risk threshold fits better than an
    unconditional rule."""

    __tablename__ = "permission_overrides"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("platform_users.id"), nullable=False)
    permission_key: Mapped[str] = mapped_column(String(128), nullable=False)
    effect: Mapped[OverrideEffect] = mapped_column(Enum(OverrideEffect), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    risk_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# SAP-003 — ABAC policy authoring: the hard Tier-3 gate
# ══════════════════════════════════════════════════════════════════

class ABACPolicyStatus(str, enum.Enum):
    DRAFT = "draft"
    SIMULATED = "simulated"
    APPLIED = "applied"
    REJECTED = "rejected"


class ABACPolicy(Base):
    """applied_at/applied_by/accountable_role are only ever set by the
    human-only apply endpoint — there is no autonomous code path to
    them anywhere in this codebase, the same structural guarantee as
    OFAC or the FTC Red Flags Rule elsewhere on this platform.
    Simulation (conflicts_detected, simulated_at) re-runs automatically
    on every edit and has no such restriction — previewing a proposed
    grant carries none of the risk that actually granting it does."""

    __tablename__ = "abac_policies"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_role_key: Mapped[str] = mapped_column(String(64), nullable=False)
    target_bundle_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ABACPolicyStatus] = mapped_column(Enum(ABACPolicyStatus), nullable=False, default=ABACPolicyStatus.DRAFT)
    conflicts_detected: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    simulated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accountable_role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    applied_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# SAP-004 — Panel & module distribution, hierarchical
# ══════════════════════════════════════════════════════════════════

class ModuleGrant(Base):
    """Which panels/modules a tenant has provisioned — every panel in
    every subsidiary console (Fleet, DMS, HRM, Mobility admin, etc.)
    is provisioned from here, per the wireframe's own caption.
    parent_module_key is null for a top-level module (fleet-management,
    dealer-center, hrm) and set to that module's key for one of its
    sub-sections (e.g. flt-003-staging's parent is fleet-management) —
    a sub-section can only be enabled if its parent module is, enforced
    in module_engine.grant_module."""

    __tablename__ = "module_grants"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    module_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_module_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# SAP-006 — Platform settings: payment gateways, branding/UI-UX,
# feature flags, and general configuration. One flexible categorized
# key-value store rather than a separate table per category — the
# categories are a display grouping, not a structural distinction.
# ══════════════════════════════════════════════════════════════════

class SettingCategory(str, enum.Enum):
    PAYMENT_GATEWAY = "payment_gateway"
    BRANDING = "branding"
    FEATURE_FLAG = "feature_flag"
    GENERAL = "general"
    AI_CONTENT_ASSIST = "ai_content_assist"
    SECURITY = "security"


class PlatformSetting(Base):
    """value is JSON so a single row can hold a structured config
    (e.g. a payment gateway's {"public_key": "...", "enabled": true})
    without needing a new column per setting. Secrets (API keys) are
    stored as-is here for this environment's purposes — a production
    deployment would route secret values through a vault and store
    only a reference here, not the value itself."""

    __tablename__ = "platform_settings"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[SettingCategory] = mapped_column(Enum(SettingCategory), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# SAP-006 — Page content management (CMS)
# ══════════════════════════════════════════════════════════════════

class PageContentStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


class PageContent(Base):
    """A page. slug/seo_title/seo_description/template are the fields
    a commercial CMS needs beyond raw body text; Section and
    SubSection below hold the page's actual structured content —
    body is kept for simple pages that don't need section-level
    structure (e.g. a plain terms-of-service page)."""

    __tablename__ = "page_contents"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    page_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    seo_title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    seo_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    template: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[PageContentStatus] = mapped_column(Enum(PageContentStatus), nullable=False, default=PageContentStatus.DRAFT)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# SAP-006 — Website section & subsection management. A page is built
# from an ordered list of sections; each section is built from an
# ordered list of subsections. Every level has its own visibility
# toggle, so a subsection can be hidden without touching its parent
# section, and a section can be hidden without deleting any of its
# subsections — full independent control at every level, per the
# requirement that every section AND subsection of every part of the
# UI be separately manageable.
# ══════════════════════════════════════════════════════════════════

class SectionType(str, enum.Enum):
    HERO = "hero"
    TEXT = "text"
    IMAGE = "image"
    GALLERY = "gallery"
    TESTIMONIAL = "testimonial"
    CTA = "cta"
    FEATURES = "features"
    FOOTER = "footer"
    CUSTOM = "custom"


class Section(Base):
    """parallax_enabled/parallax_speed/parallax_image_url live here,
    not on a separate table — parallax is a per-section display
    property, the same way font size or padding would be, not an
    independent content entity."""

    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    page_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("page_contents.id"), nullable=False)
    section_key: Mapped[str] = mapped_column(String(128), nullable=False)
    section_type: Mapped[SectionType] = mapped_column(Enum(SectionType), nullable=False, default=SectionType.CUSTOM)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    parallax_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parallax_speed: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    parallax_image_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SubSection(Base):
    """A content block within a section — the level below Section,
    completing the two-level 'section and subsection' hierarchy."""

    __tablename__ = "subsections"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    section_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("sections.id"), nullable=False)
    subsection_key: Mapped[str] = mapped_column(String(128), nullable=False)
    block_type: Mapped[str] = mapped_column(String(64), nullable=False, default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# SAP-006 — Theming: named color themes, each a full palette. Only
# one theme is active per tenant at a time — activating a theme
# autonomously deactivates whichever theme was active before, so
# "active" is always a real, single-valued state, never ambiguous.
# ══════════════════════════════════════════════════════════════════

class Theme(Base):
    __tablename__ = "themes"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ThemeColorKey(str, enum.Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    ACCENT = "accent"
    BACKGROUND = "background"
    SURFACE = "surface"
    TEXT = "text"
    TEXT_MUTED = "text_muted"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"
    BORDER = "border"


class ThemeColor(Base):
    __tablename__ = "theme_colors"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    theme_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("themes.id"), nullable=False)
    color_key: Mapped[ThemeColorKey] = mapped_column(Enum(ThemeColorKey), nullable=False)
    hex_value: Mapped[str] = mapped_column(String(9), nullable=False)


# ══════════════════════════════════════════════════════════════════
# SAP-006 — Media library
# ══════════════════════════════════════════════════════════════════

class MediaCategory(str, enum.Enum):
    HERO = "hero"
    GALLERY = "gallery"
    ICON = "icon"
    BACKGROUND = "background"
    GENERAL = "general"


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    category: Mapped[MediaCategory] = mapped_column(Enum(MediaCategory), nullable=False, default=MediaCategory.GENERAL)
    uploaded_by: Mapped[str] = mapped_column(String(128), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# SAP-005 — Platform-wide audit log
# ══════════════════════════════════════════════════════════════════

class ActorType(str, enum.Enum):
    HUMAN = "human"
    AI = "ai"


class AuditLogEntry(Base):
    """Distinct from ai-orchestration-svc's DecisionLog: DecisionLog
    is AI-decision-specific (reasoning, tools_called, an agent_key).
    This table is broader — every human admin action on this platform
    (role assignment, override grant, policy application, module
    grant) gets a row here too, so 'who did what' is answerable
    platform-wide, not just for AI-originated actions."""

    __tablename__ = "audit_log_entries"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_type: Mapped[ActorType] = mapped_column(Enum(ActorType), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(String(256), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ══════════════════════════════════════════════════════════════════
# Integrations — third-party credentials (Anthropic, SMTP, Twilio, FCM,
# social platforms, image generation), set by the super admin per
# tenant. The config is encrypted at rest; the API only ever returns it
# masked, and services read it decrypted through /internal/ only.
# ══════════════════════════════════════════════════════════════════

class IntegrationCredential(Base):
    __tablename__ = "integration_credentials"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", name="uq_integration_tenant_provider"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    config_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    test_status: Mapped[str] = mapped_column(String(16), nullable=False, default="untested")  # untested / ok / failed
    test_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
