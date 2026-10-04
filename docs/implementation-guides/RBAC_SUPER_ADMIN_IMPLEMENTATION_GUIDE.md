# RBAC & Super Admin Panel
## Implementation Guide

A step-by-step guide to implementing the platform's Super Admin Panel: a standalone backend service (`rbac-svc`) plus a thin-client frontend that holds no business logic of its own. Covers the full scope — global tenant directory, RBAC, ABAC policy authoring, hierarchical module/panel distribution, the platform-wide audit log, and the SAP-006 configuration suite (platform settings, page management, website builder with sections/subsections and parallax, color theming, media library, and AI content-assist settings) — plus AI agent control wired directly to the orchestration layer.

**Governing principle:** every capability executes autonomously by default. Exactly two human-only gates exist on this platform, and they are deliberately different shapes:

1. **A hard, unconditional gate** — ABAC policy application. No risk score, no threshold, and no AI code path reaches it; it structurally requires a named accountable role and a prior simulation, full stop. This is the same shape as a Red-Flags-Rule override elsewhere on the platform: a missing required human input raises before anything else executes.
2. **A threshold-based review gate** — permission-override risk monitoring. Below a configurable risk score, an override is auto-revoked as clearly unwarranted; at or above it, the override is flagged and queued for a human instead of being acted on unilaterally. An identity/access anomaly is exactly the kind of ambiguous case a threshold fits better than an unconditional rule — unlike the ABAC gate, there is a real number here, and calibrating it is a legitimate admin decision.

Everything else — provisioning a tenant, assigning an already-defined role, granting a module, changing a payment gateway's config, editing page content, activating a theme — is autonomous with no gate at all, because none of it is an access-control decision.

---

## Step 1 — Scaffold the service

```
services/rbac-svc/
├── requirements.txt
├── alembic.ini
├── pytest.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── src/
│   ├── main.py
│   ├── core/          (config, database, security, kafka_events)
│   ├── models/         (single __init__.py holding every table)
│   ├── services/       (one engine module per capability area)
│   └── api/             (one router module per capability area)
└── tests/
    ├── conftest.py
    └── test_*.py        (one file per capability area)
```

`requirements.txt`, `pytest.ini`, `alembic.ini`, and the core `database.py`/`security.py`/`kafka_events.py` follow the exact same pattern used across every other service on this platform: an async SQLAlchemy engine with a cross-dialect `GUID` type (native UUID on PostgreSQL, `CHAR(36)` on SQLite), a `get_tenant_id` dependency that hard-requires the `X-Tenant-Id` header, and a fire-and-forget Kafka publisher that logs and continues on failure rather than blocking a request.

`src/core/config.py` holds every tunable in one place — never a magic number buried in a function body:

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RBAC_", extra="ignore")

    service_name: str = "rbac-svc"
    port: int = 8011
    database_url: str = "sqlite+aiosqlite:///./rbac.db"
    kafka_bootstrap_servers: str = "kafka:9092"
    ai_orchestration_svc_base_url: str = "http://ai-orchestration-svc:8020"
    ai_initiated_header_value: str = "compliance_audit_agent"
    deny_wins: bool = True  # enforced in code, not just configured


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

---

## Step 2 — Enable CORS from the start

The Super Admin Panel is a static HTML file with no build step or gateway in front of it — the browser calls `rbac-svc` (and `ai-orchestration-svc`, for agent control) directly. Add CORS in `main.py` before anything else, with a comment marking it as an environment-specific choice:

```python
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="rbac-svc", version="1.0.0", lifespan=lifespan)

# The Super Admin Panel calls this service directly from the browser —
# CORS has to be open here for that to work at all. In production this
# narrows to the gateway's origin only, per the platform's
# gateway-only-ingress principle.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Apply the identical block to every other service the panel will call directly — in this case `ai-orchestration-svc`, for the AI Agent Control and Goal State pages. Missing this on even one service produces a confusing, hard-to-diagnose failure: the request never reaches the route handler at all, so server-side logging shows nothing — the rejection happens in the browser before the request is sent.

---

## Step 3 — Data model

Build one `src/models/__init__.py` with every table, grouped by capability area. Use `str, enum.Enum` for every status/category field so values serialize cleanly through Pydantic.

### SAP-001 — Global Tenant Directory

```python
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    subsidiary: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TenantStatus] = mapped_column(Enum(TenantStatus), nullable=False, default=TenantStatus.ACTIVE)
```

### SAP-002 — RBAC

Four tables: `Role` (key, name, `permission_keys` as a JSON list, `wildcard_permissions`, `is_system_role`), `PlatformUser`, `RoleAssignment` (a plain join between user and role), and `PermissionOverride` — the one table in this section with real weight:

```python
class PermissionOverride(Base):
    """DENY always wins over any ALLOW from a role or another
    override — enforced in the resolution engine, not just here.
    risk_flagged and revoked* are written exclusively by the
    threshold-based monitoring loop; there is no autonomous path to
    an unconditional revoke, because an identity/access anomaly is
    the ambiguous case that loop exists for."""
    __tablename__ = "permission_overrides"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("platform_users.id"), nullable=False)
    permission_key: Mapped[str] = mapped_column(String(128), nullable=False)
    effect: Mapped[OverrideEffect] = mapped_column(Enum(OverrideEffect), nullable=False)  # allow | deny
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    risk_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

### SAP-003 — ABAC

```python
class ABACPolicy(Base):
    """applied_at/applied_by/accountable_role are set exclusively by
    the human-only apply path — no autonomous code anywhere writes to
    them. Simulation (conflicts_detected, simulated_at) carries none
    of that restriction: previewing a proposed grant is not the same
    risk as actually granting it."""
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
```

### SAP-004 — Hierarchical module & panel distribution

```python
class ModuleGrant(Base):
    """parent_module_key is null for a top-level module and set to
    that module's key for one of its sub-sections. A sub-section can
    only be enabled while its parent is — enforced in the engine, not
    the UI — and revoking a parent cascades to disable every child."""
    __tablename__ = "module_grants"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    module_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_module_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
```

### SAP-005 — Platform-wide audit log

```python
class AuditLogEntry(Base):
    """Distinct from any single agent's own AI decision log: this
    table records every human AND every AI action across the entire
    platform, so 'who did what' is answerable regardless of which
    system originated the change."""
    __tablename__ = "audit_log_entries"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_type: Mapped[ActorType] = mapped_column(Enum(ActorType), nullable=False)  # human | ai
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(String(256), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```

### SAP-006 — Platform configuration suite

**Platform settings** — one flexible categorized key-value store rather than a table per category. Payment gateways, branding flags, feature flags, general config, and AI content-assist settings are all genuinely the same *shape* of thing (a category, a key, a JSON value, who last touched it) even though they mean very different things:

```python
class SettingCategory(str, enum.Enum):
    PAYMENT_GATEWAY = "payment_gateway"
    BRANDING = "branding"
    FEATURE_FLAG = "feature_flag"
    GENERAL = "general"
    AI_CONTENT_ASSIST = "ai_content_assist"


class PlatformSetting(Base):
    __tablename__ = "platform_settings"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[SettingCategory] = mapped_column(Enum(SettingCategory), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
```

**Enumerate every category the frontend will actually write to before shipping the enum.** A category that exists only in JavaScript and not in the backend's `Enum` type produces a 422 on every save — and if the frontend's error handling is graceful (as it should be, matching Step 9 below), that failure is silent: the UI shows no crash, just data that never persisted. Write the frontend's save function and the backend's enum values from the same list, not from memory in two places.

**Page management, extended for a real CMS:**

```python
class PageContent(Base):
    __tablename__ = "page_contents"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    page_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    seo_title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    seo_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    template: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")  # for simple pages with no section structure
    status: Mapped[PageContentStatus] = mapped_column(Enum(PageContentStatus), nullable=False, default=PageContentStatus.DRAFT)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
```

**Website builder — sections and subsections, two independently-controllable levels:**

```python
class Section(Base):
    """parallax_enabled/parallax_speed/parallax_image_url live here —
    parallax is a per-section display property, not an independent
    content entity that needs its own table."""
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


class SubSection(Base):
    """The level below Section, completing the section/subsection
    hierarchy. Its own visible flag is fully independent of the
    parent section's — hiding one must never touch the other."""
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
```

**Theming — named palettes, exactly one active at a time:**

```python
class Theme(Base):
    __tablename__ = "themes"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ThemeColorKey(str, enum.Enum):
    PRIMARY = "primary"; SECONDARY = "secondary"; ACCENT = "accent"
    BACKGROUND = "background"; SURFACE = "surface"
    TEXT = "text"; TEXT_MUTED = "text_muted"
    SUCCESS = "success"; WARNING = "warning"; ERROR = "error"; INFO = "info"
    BORDER = "border"


class ThemeColor(Base):
    __tablename__ = "theme_colors"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    theme_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("themes.id"), nullable=False)
    color_key: Mapped[ThemeColorKey] = mapped_column(Enum(ThemeColorKey), nullable=False)
    hex_value: Mapped[str] = mapped_column(String(9), nullable=False)
```

**Media library:**

```python
class MediaAsset(Base):
    __tablename__ = "media_assets"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    category: Mapped[MediaCategory] = mapped_column(Enum(MediaCategory), nullable=False, default=MediaCategory.GENERAL)
    uploaded_by: Mapped[str] = mapped_column(String(128), nullable=False)
```

---

## Step 4 — Build the engine layer

One module per capability area. Every function that represents an autonomous decision computes the outcome and writes it in the same call — never a `record()` followed by a separate `apply()` a caller has to remember to chain.

### 4.1 — RBAC: deny-wins resolution and the threshold review loop (`rbac_engine.py`)

Deny-wins has to be built as an explicit two-pass computation — collect everything a role allows, then let overrides adjust that set, with DENY checked last and unconditionally:

```python
async def resolve_effective_permissions(db, tenant_id, user_id) -> dict:
    assignments = await get_user_role_assignments(db, tenant_id, user_id)
    allowed: set[str] = set()
    for assignment in assignments:
        role = await get_role(db, assignment.role_id)
        allowed.update(role.permission_keys)
        allowed.update(role.wildcard_permissions)

    overrides = await get_user_permission_overrides(db, tenant_id, user_id, active_only=True)
    denied: set[str] = set()
    for override in overrides:
        if override.effect == OverrideEffect.ALLOW:
            allowed.add(override.permission_key)
        else:
            denied.add(override.permission_key)

    effective = allowed - denied  # deny always wins, regardless of source
    return {"user_id": str(user_id), "effective_permissions": sorted(effective), "denied": sorted(denied)}
```

The risk-threshold loop needs a scoring function and an action function, kept separate so the scoring logic is independently testable:

```python
RISK_AUTO_REVOKE_KEYWORDS = ("wildcard", "*", "super_admin", "rbac.grant", "abac.apply")

def score_override_risk(override) -> tuple[int, list[str]]:
    """A DENY override is structurally exempt, not merely scored
    low — see the note below on why this distinction is load-bearing."""
    if override.effect == OverrideEffect.DENY:
        return 0, ["deny overrides narrow access; never auto-revoked as 'unwarranted'"]
    score, drivers = 0, []
    if any(kw in override.permission_key.lower() for kw in RISK_AUTO_REVOKE_KEYWORDS):
        score += 60; drivers.append(f"permission key '{override.permission_key}' matches a broad/escalating pattern")
    if not override.reason or len(override.reason.strip()) < 10:
        score += 25; drivers.append("no meaningful justification on file")
    return min(100, score), drivers


async def auto_revoke_if_unwarranted(db, tenant_id, override_id, threshold, revoked_by) -> tuple[PermissionOverride, bool]:
    override = await get_override(db, tenant_id, override_id)

    # The exemption is a hard type check, not a score comparison. A
    # scoring function that returns 0 for "exempt" would otherwise
    # satisfy "score < threshold" and trigger exactly the revoke this
    # exemption exists to prevent — checking the effect directly,
    # before any score is even computed, closes that gap structurally.
    if override.effect == OverrideEffect.DENY:
        return override, False

    risk_score, drivers = score_override_risk(override)
    override.risk_flagged = risk_score >= threshold
    if risk_score < threshold and not override.revoked:
        override.revoked = True
        override.revoked_by = revoked_by
        override.revoked_reason = f"[deterministic] Auto-revoked, risk score {risk_score} below threshold {threshold}: {'; '.join(drivers)}."
        await db.commit()
        return override, True
    await db.commit()
    return override, False
```

Write the test for the DENY exemption as its own case, not as an assumption folded into a broader test — a low-scoring exemption and a genuinely low-risk override look identical from the score alone; only an explicit `effect == DENY` check distinguishes them, and only a dedicated test catches a regression that removes it.

### 4.2 — ABAC: simulation is free, application is a hard gate (`abac_engine.py`)

```python
async def simulate(db, tenant_id, policy_id) -> ABACPolicy:
    """Re-runs on every call, no restriction — a preview carries none
    of the risk an actual grant does."""
    policy = await get_policy(db, tenant_id, policy_id)
    policy.conflicts_detected = await detect_conflicts(db, tenant_id, policy.target_bundle_key)
    policy.simulated_at = datetime.now(timezone.utc)
    if policy.status == ABACPolicyStatus.DRAFT:
        policy.status = ABACPolicyStatus.SIMULATED
    await db.commit()
    return policy


class AccountableRoleRequiredError(ValueError): pass
class PolicyNotSimulatedError(ValueError): pass


async def apply_policy(db, tenant_id, policy_id, accountable_role, applied_by) -> ABACPolicy:
    """Structurally refuses without a real, non-empty accountable
    role and without a prior simulation — no risk score, no
    threshold, and no bypass anywhere in this function."""
    if not accountable_role or not accountable_role.strip():
        raise AccountableRoleRequiredError("an accountable role must be named before a policy can be applied")
    policy = await get_policy(db, tenant_id, policy_id)
    if policy.simulated_at is None:
        raise PolicyNotSimulatedError("a policy must be simulated at least once before it can be applied")
    policy.status = ABACPolicyStatus.APPLIED
    policy.accountable_role = accountable_role.strip()
    policy.applied_by = applied_by
    policy.applied_at = datetime.now(timezone.utc)
    await db.commit()
    return policy
```

Map both exceptions to real HTTP status codes at the API layer — 422 for the missing accountable role, 409 for skipping simulation — so a frontend can distinguish "you forgot a required field" from "you did things in the wrong order" rather than surfacing one generic error for both.

### 4.3 — Hierarchical modules, with the parent-enabled constraint and cascade revoke (`module_engine.py`)

```python
class ParentModuleDisabledError(ValueError): pass

async def grant_module(db, tenant_id, module_key, granted_by, parent_module_key=None, label="") -> ModuleGrant:
    if parent_module_key:
        parent = await _get_grant(db, tenant_id, parent_module_key)
        if parent is None or not parent.enabled:
            raise ParentModuleDisabledError(f"parent module '{parent_module_key}' must be granted and enabled before its sub-section can be")
    existing = await _get_grant(db, tenant_id, module_key)
    if existing:
        existing.enabled = True
        existing.granted_by = granted_by
        await db.commit()
        return existing
    grant = ModuleGrant(tenant_id=tenant_id, module_key=module_key, parent_module_key=parent_module_key, label=label or module_key, granted_by=granted_by)
    db.add(grant)
    await db.commit()
    return grant


async def revoke_module(db, tenant_id, module_key) -> ModuleGrant | None:
    """Revoking a parent autonomously cascades to every child grant —
    a sub-section reachable only through a disabled parent isn't
    meaningfully enabled either way."""
    grant = await _get_grant(db, tenant_id, module_key)
    if grant is None:
        return None
    grant.enabled = False
    children = await _get_children(db, tenant_id, module_key)
    for child in children:
        child.enabled = False
    await db.commit()
    return grant
```

### 4.4 — Website sections and subsections, independently visible (`section_engine.py`)

The critical property to preserve here is that a `visible` flag on one level never touches the other level. Implement `update_section` and `update_subsection` as separate generic field-update functions — never a single function that happens to also flip a child's visibility as a side effect, even if that side effect seems convenient in the moment:

```python
async def update_section(db, tenant_id, section_id, updates: dict, updated_by) -> Section | None:
    """A single generic update path for every editable field — title,
    visibility, parallax — so there's exactly one place that stamps
    updated_by, and no code path that touches a subsection's
    visibility as a side effect of a section-level edit."""
    section = await get_section(db, tenant_id, section_id)
    if section is None:
        return None
    for key, value in updates.items():
        if hasattr(section, key):
            setattr(section, key, value)
    section.updated_by = updated_by
    await db.commit()
    return section


async def delete_section(db, tenant_id, section_id) -> bool:
    """Deleting a section cascades to delete its subsections — a
    subsection with no parent section isn't addressable from any
    page. This is the one place a section-level action legitimately
    touches its children, because there's no meaningful 'independent'
    state for a subsection whose parent no longer exists."""
    section = await get_section(db, tenant_id, section_id)
    if section is None:
        return False
    for sub in await list_subsections(db, tenant_id, section_id):
        await db.delete(sub)
    await db.delete(section)
    await db.commit()
    return True
```

Write a test that hides a subsection and asserts the parent section is still visible, and a companion test that hides the section and asserts every subsection's visibility is untouched. These two tests look almost identical and it's tempting to write only one — write both, since a regression that couples the two levels could break either direction independently.

### 4.5 — Theming, with the single-active-theme guarantee (`theme_engine.py`)

```python
DEFAULT_PALETTE = {
    ThemeColorKey.PRIMARY: "#c9a84c", ThemeColorKey.SECONDARY: "#3d8ed4", ThemeColorKey.ACCENT: "#7b6cd4",
    ThemeColorKey.BACKGROUND: "#07090f", ThemeColorKey.SURFACE: "#0d1320",
    ThemeColorKey.TEXT: "#edeae0", ThemeColorKey.TEXT_MUTED: "#6e8096",
    ThemeColorKey.SUCCESS: "#2ea87a", ThemeColorKey.WARNING: "#c9a84c", ThemeColorKey.ERROR: "#d05050", ThemeColorKey.INFO: "#3d8ed4",
    ThemeColorKey.BORDER: "#ffffff0f",
}

async def create_theme(db, tenant_id, name, seed_defaults=True) -> Theme:
    theme = Theme(tenant_id=tenant_id, name=name)
    db.add(theme)
    await db.commit()
    if seed_defaults:
        for color_key, hex_value in DEFAULT_PALETTE.items():
            db.add(ThemeColor(tenant_id=tenant_id, theme_id=theme.id, color_key=color_key, hex_value=hex_value))
        await db.commit()
    return theme


async def activate_theme(db, tenant_id, theme_id) -> Theme | None:
    """Deactivates every other theme for this tenant in the same
    call — exactly one theme is ever active, structurally, never by
    convention the caller has to remember to enforce."""
    theme = await get_theme(db, tenant_id, theme_id)
    if theme is None:
        return None
    for t in await list_themes(db, tenant_id):
        t.is_active = (t.id == theme_id)
    await db.commit()
    return theme
```

Test this by activating theme A, asserting it's active, activating theme B, and asserting theme A is no longer active *and* that exactly one theme across the whole list has `is_active=True`. Testing only "B is now active" would pass even if the deactivation of A were silently broken.

### 4.6 — Settings, content, and media (`settings_engine.py`, `content_engine.py`, `media_engine.py`)

These three are the simplest engines in the service, deliberately — every write is a configuration or content change, not an access-control decision, so there's no gate to build at all. The only discipline worth calling out: use an upsert pattern (update if the key/page_key already exists, insert otherwise) rather than allowing silent duplicates:

```python
async def upsert_setting(db, tenant_id, category, key, value, updated_by) -> PlatformSetting:
    existing = await _get_setting(db, tenant_id, category, key)
    if existing:
        existing.value = value
        existing.updated_by = updated_by
        await db.commit()
        return existing
    setting = PlatformSetting(tenant_id=tenant_id, category=category, key=key, value=value, updated_by=updated_by)
    db.add(setting)
    await db.commit()
    return setting
```

---

## Step 5 — Build the API layer

One router per capability area, each declaring its own full path prefix. Map every engine exception to a specific, distinguishable HTTP status:

```python
@router.post("/{policy_id}/apply", response_model=PolicyOut)
async def apply_policy(policy_id, body, tenant_id, db):
    try:
        policy = await abac_engine.apply_policy(db, tenant_id, policy_id, body.accountable_role, body.applied_by)
    except abac_engine.AccountableRoleRequiredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except abac_engine.PolicyNotSimulatedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit_engine.record(db, tenant_id, "human", body.applied_by, "abac_policy.applied", str(policy_id), f"accountable_role={body.accountable_role}")
    return policy
```

For update endpoints on a section or subsection, accept a partial-update body and only apply the fields that were actually sent, so toggling one property never requires resending the whole object:

```python
class SectionUpdate(BaseModel):
    title: str | None = None
    visible: bool | None = None
    parallax_enabled: bool | None = None
    parallax_speed: float | None = None
    parallax_image_url: str | None = None
    order_index: int | None = None
    updated_by: str = "super-admin"

@router.patch("/{section_id}", response_model=SectionOut)
async def update_section(section_id, body, tenant_id, db):
    updates = {k: v for k, v in body.model_dump(exclude={"updated_by"}).items() if v is not None}
    section = await section_engine.update_section(db, tenant_id, section_id, updates, body.updated_by)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found")
    return section
```

Wire an `audit_engine.record()` call into every mutating endpoint across every router, not just the ones that feel security-relevant — the platform-wide audit log's entire value is that "who did what" is answerable for *everything*, and a gap in coverage only becomes visible the first time someone needs the record that isn't there.

---

## Step 6 — Assemble the application

```python
# src/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api import abac, audit, content, media, modules, rbac, sections, settings, tenants, theme
from src.core.database import init_models

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield

app = FastAPI(title="rbac-svc", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for router_module in (tenants, rbac, abac, modules, audit, settings, content, sections, theme, media):
    app.include_router(router_module.router)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "rbac-svc"}
```

---

## Step 7 — Generate the migration, and plan for enum growth

```bash
alembic revision --autogenerate -m "initial rbac-svc schema"
```

**Check the generated file for a missing import.** Autogenerate sometimes emits a `GUID()` column type reference without the corresponding `import src.core.database` at the top — add it manually if so.

**Adding a new value to an existing enum needs its own migration, written by hand.** Autogenerate does not reliably detect enum value additions across dialects, and the two dialects need different handling:

```python
"""add ai_content_assist setting category"""
from alembic import op

revision = '...'
down_revision = '...'

def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE settingcategory ADD VALUE IF NOT EXISTS 'AI_CONTENT_ASSIST'")
    # SQLite has no native enum type — nothing to migrate there.

def downgrade() -> None:
    # PostgreSQL cannot remove a value from an existing enum type
    # without recreating it; no-op is the safe default rather than
    # risking data loss on any row already using the value.
    pass
```

Any settings category, module key, or status value the frontend will ever POST needs to exist in the backend's enum *before* the frontend ships a form that can write it. Treat "does this exact string exist in the enum" as a checklist item when adding a new settings category or content type, not something to catch later — a missing enum value produces a 422 that a well-behaved frontend catches and displays as a quiet error banner, which means the failure can sit unnoticed indefinitely rather than crashing loudly.

---

## Step 8 — Write the test suite

Follow the standard fixture pattern: a `StaticPool` in-memory SQLite engine shared between a raw `db_session` fixture and an `AsyncClient` fixture, with a fixed `TENANT_HEADERS` constant every test reuses.

For each capability area, the tests worth writing are the ones that check a *guarantee*, not just a happy path:

- **RBAC**: deny beats an ALLOW from a role; deny beats a conflicting ALLOW from another override; a narrow unjustified override is auto-revoked; a broad wildcard-pattern override is escalated and *not* touched either way; a DENY-effect override is never auto-revoked regardless of what its score would otherwise be.
- **ABAC**: apply rejects an empty accountable role (422); apply rejects whitespace-only (422, not just falsy-string handling); apply rejects an unsimulated policy (409); simulate can be called repeatedly with no side effects beyond refreshing `conflicts_detected`; apply succeeds and records both `accountable_role` and `applied_by`.
- **Modules**: a sub-section can't be granted before its parent exists and is enabled (409); granting is possible once the parent is enabled; revoking a parent cascades to disable every child.
- **Sections**: hiding a subsection doesn't change its section's visibility, and hiding a section doesn't change any subsection's visibility (write both directions); parallax fields round-trip correctly; deleting a section cascades to its subsections.
- **Theme**: creating seeds the full default palette; setting a color updates in place rather than duplicating; activating a second theme deactivates the first, and exactly one theme across the whole list is ever active.
- **Settings**: every category the frontend will write to has a passing round-trip test using that exact category string, not a placeholder.

```bash
pytest tests/ -q
```

---

## Step 9 — Build the frontend as a thin client, not a second implementation

The single most important discipline for this frontend: it holds zero business logic. Deny-wins resolution, risk scoring, the ABAC gate, the parallax fields, the single-active-theme rule — none of it gets reimplemented in JavaScript. Every page-level function is a `fetch()` call to the real endpoint, and the render function displays whatever comes back.

### 9.1 — The API client

```javascript
var RBAC_API_BASE = window.RBAC_API_BASE || 'http://localhost:8011';
var AI_ORCH_API_BASE = window.AI_ORCH_API_BASE || 'http://localhost:8020';
var TENANT_ID = 'voyqi-holdings';
var CURRENT_USER = 'garth';

async function api(method, path, body) {
  var opts = { method: method, headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': TENANT_ID } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  var resp;
  try {
    resp = await fetch(RBAC_API_BASE + path, opts);
  } catch (networkErr) {
    throw { status: 0, detail: 'Cannot reach rbac-svc at ' + RBAC_API_BASE + '. Is it running? (' + networkErr.message + ')' };
  }
  var data = null;
  try { data = await resp.json(); } catch (e) { /* empty body, fine */ }
  if (!resp.ok) throw { status: resp.status, detail: (data && data.detail) || resp.statusText };
  return data;
}
```

A second, near-identical wrapper (`aiApi`) targets `AI_ORCH_API_BASE` for the AI Agent Control and Goal State pages, carrying an `X-Actor-Id` header those endpoints use for attribution instead of relying on the tenant header alone.

### 9.2 — Every render function must survive an unreachable backend

```javascript
async function renderTenants() {
  var tenants;
  try {
    tenants = await api('GET', '/api/v1/rbac/tenants');
  } catch (err) {
    connectionBanner(err);
    document.getElementById('tenant-rows').innerHTML = '<div style="color:var(--red)">Could not load tenants — see banner above.</div>';
    return;
  }
  document.getElementById('tenant-rows').innerHTML = tenants.map(renderTenantRow).join('');
}
```

`connectionBanner` writes a visible, specific message naming the unreachable service — never fail silently, and never let one failed `render*()` call break `rerenderAll()`'s ability to render every other page. Test this specifically: load the page with no backend running at all, call every `render*()` function, and assert none of them throw an uncaught exception. This is a real, cheap test to write and it catches a whole category of bug before it reaches a browser.

### 9.3 — Surfacing the ABAC hard gate in the UI

The backend's 422/409 distinction should reach the person using the panel as two different, specific messages, not one generic "error occurred":

```javascript
async function applyPolicyUI(id) {
  var role = document.getElementById('accountable-' + id).value;
  try {
    await api('POST', '/api/v1/rbac/abac-policies/' + id + '/apply', { accountable_role: role, applied_by: CURRENT_USER });
    await renderPolicies();
  } catch (err) {
    var errEl = document.getElementById('policy-err-' + id);
    errEl.style.display = 'block';
    errEl.textContent = err.status === 422
      ? '422 — ' + err.detail + '. No threshold or AI path reaches this gate.'
      : (err.status === 409 ? '409 — ' + err.detail : ('rbac-svc error ' + err.status + ': ' + err.detail));
  }
}
```

### 9.4 — Verify against a real running backend, not just mocks

A unit test with a mocked `fetch` confirms the call sites use the right function signatures. It does not confirm the two services' contracts actually match — a URL typo, a field-name mismatch, or a settings category that exists in the frontend but not the backend's enum will all pass a mocked test and fail silently in production. Before considering an integration done, run the real backend and drive the actual page functions against it with no mocking at all:

```bash
# terminal 1
cd services/rbac-svc && RBAC_DATABASE_URL="sqlite+aiosqlite:///./rbac.db" uvicorn src.main:app --port 8011
```

```javascript
// terminal 2 — load the real HTML file in a browser-like environment
// with a real fetch pointed at the real running server, then call
// the actual exported page functions and check the real result:
window.fetch = fetch; // Node's native fetch, or the browser's own
window.RBAC_API_BASE = 'http://127.0.0.1:8011';

document.getElementById('sf-category').value = 'ai_content_assist';
document.getElementById('sf-key').value = 'config';
document.getElementById('sf-value').value = JSON.stringify({ brand_voice: 'Confident, precise.' });
await upsertSetting();

// Then reload from the backend — not from whatever the DOM already
// shows, since that could just be reflecting the form input back —
// and confirm the value actually persisted server-side.
document.getElementById('sf-value').value = '';
await renderSettings();
```

This exact check — save, clear the local field, reload from the server, and confirm the value survived the round trip — is the one test worth running for every settings-style page before shipping it. A save button that appears to work but silently 422s under the hood is invisible to every other kind of test.

---

## Step 10 — Deployment checklist

- [ ] `RBAC_DATABASE_URL` points at a real Postgres instance in every non-local environment; SQLite is for tests only
- [ ] CORS `allow_origins` narrows from `["*"]` to the gateway's actual origin before this reaches a production environment
- [ ] `AI_ORCH_API_BASE` (frontend) and the corresponding CORS config on `ai-orchestration-svc` both point at the real deployed address, not `localhost`
- [ ] Every `SettingCategory`, `ModuleKey`, and `ContentType`-equivalent value referenced anywhere in the frontend's JavaScript has a matching entry in the backend's enum — grep the frontend for every string literal passed to a `category`/`type` field and cross-check against the backend model file directly, don't rely on memory
- [ ] The ABAC apply endpoint and the permission-override auto-revoke endpoint are covered by monitoring on their block/flag rates — a sudden spike or a sudden drop to zero on either is worth a human looking at
- [ ] Migration applies cleanly against a fresh database, including every hand-written enum-addition migration, before any traffic is routed to the new schema
- [ ] Confirm no code path anywhere calls the ABAC apply function with a hardcoded or default `accountable_role` — grep for the function name across the whole codebase, not just `rbac-svc` itself, since a convenience wrapper written elsewhere is the most likely place a hard gate quietly grows a bypass
