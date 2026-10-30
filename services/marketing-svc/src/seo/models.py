"""Tables of the Growth / SEO engine. Every row belongs to a tenant and
(nearly always) a site; ids are UUIDs, times are timezone-aware UTC."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base
from src.seo.constants import DEFAULT_CAPS


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(GUID, primary_key=True, default=uuid.uuid4)


def _tenant() -> Mapped[str]:
    return mapped_column(String(64), nullable=False, index=True)


def _site() -> Mapped[uuid.UUID]:
    return mapped_column(GUID, ForeignKey("seo_sites.id"), nullable=False, index=True)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class SeoSite(Base):
    """A website the SEO agents work on, with its business profile and
    the autonomy rules they work under."""

    __tablename__ = "seo_sites"
    __table_args__ = (UniqueConstraint("tenant_id", "domain", name="uq_seo_sites_tenant_domain"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    domain: Mapped[str] = mapped_column(String(256), nullable=False)
    # description, positioning, brand_terms, products, markets, topics
    business_profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    goals: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    autopilot_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="assisted")
    autonomy_overrides: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    caps: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: dict(DEFAULT_CAPS))
    report_frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="weekly")
    report_recipients: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    authorized_requesters: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_cycle_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()


class SeoIntegration(Base):
    """How the site reaches one kind of outside system. Holds settings and
    a credential_ref only — never a secret."""

    __tablename__ = "seo_integrations"
    __table_args__ = (UniqueConstraint("site_id", "kind", name="uq_seo_integrations_site_kind"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # not_connected / configured / connected / error
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_connected")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SeoTask(Base):
    """One unit of agent work. Agents only ever pick up `ready` tasks the
    autopilot plan says may run now."""

    __tablename__ = "seo_tasks"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    impact: Mapped[float] = mapped_column(Float, nullable=False, default=50)
    effort: Mapped[float] = mapped_column(Float, nullable=False, default=3)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    priority: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    # backlog / ready / awaiting_approval / in_progress / done / failed / blocked / dismissed
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ready", index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual/strategy/email/agent/autopilot
    owning_agent: Mapped[str] = mapped_column(String(64), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approval_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = _created()
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SeoStrategy(Base):
    __tablename__ = "seo_strategies"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    narrative: Mapped[str] = mapped_column(Text, nullable=False, default="")
    initiatives: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    inputs_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = _created()


class SeoCompetitor(Base):
    __tablename__ = "seo_competitors"
    __table_args__ = (UniqueConstraint("site_id", "domain", name="uq_seo_competitors_site_domain"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    domain: Mapped[str] = mapped_column(String(256), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    authority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = _created()


class SeoKeyword(Base):
    """A search term with its demand, difficulty, who ranks for it and how
    well. `owner` is the site's own domain or a competitor's."""

    __tablename__ = "seo_keywords"
    __table_args__ = (UniqueConstraint("site_id", "term", "owner", name="uq_seo_keywords_site_term_owner"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    term: Mapped[str] = mapped_column(String(300), nullable=False)
    owner: Mapped[str] = mapped_column(String(256), nullable=False)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpc_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    intent: Mapped[str] = mapped_column(String(16), nullable=False, default="informational")
    purchase_intent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opportunity: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    cluster: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    trend: Mapped[str] = mapped_column(String(16), nullable=False, default="flat")
    growth_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_question: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_gap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    updated_at: Mapped[datetime] = _created()


class SeoRankSnapshot(Base):
    """Where the site ranked for a keyword on a day (search console or a
    rank check)."""

    __tablename__ = "seo_rank_snapshots"
    __table_args__ = (UniqueConstraint("site_id", "keyword", "on", "source", name="uq_seo_rank_site_kw_day_src"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    keyword: Mapped[str] = mapped_column(String(300), nullable=False)
    on: Mapped[date] = mapped_column(Date, nullable=False)
    position: Mapped[float | None] = mapped_column(Float, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="search_console")


class SeoOrganicDaily(Base):
    """Search Console totals for a day."""

    __tablename__ = "seo_organic_daily"
    __table_args__ = (UniqueConstraint("site_id", "day", name="uq_seo_organic_site_day"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    day: Mapped[date] = mapped_column(Date, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position: Mapped[float | None] = mapped_column(Float, nullable=True)


class SeoPageStat(Base):
    """Search Console figures for one page over a 28-day window — the
    current one and the one before, for spotting decay."""

    __tablename__ = "seo_page_stats"
    __table_args__ = (UniqueConstraint("site_id", "url", "window", name="uq_seo_page_stats_site_url_window"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    window: Mapped[str] = mapped_column(String(16), nullable=False)  # current / previous
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_query: Mapped[str | None] = mapped_column(String(300), nullable=True)
    synced_at: Mapped[datetime] = _created()


class SeoTrafficDaily(Base):
    """Web analytics totals for a day."""

    __tablename__ = "seo_traffic_daily"
    __table_args__ = (UniqueConstraint("site_id", "day", name="uq_seo_traffic_site_day"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    day: Mapped[date] = mapped_column(Date, nullable=False)
    sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    organic_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SeoResearchItem(Base):
    __tablename__ = "seo_research_items"
    __table_args__ = (UniqueConstraint("site_id", "source", "url", name="uq_seo_research_site_source_url"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    source: Mapped[str] = mapped_column(String(24), nullable=False)  # serp/competitor/reddit/news/youtube/multi_engine
    engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    query: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    signals: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Instruction-like text found in it: excluded from what agents read.
    injection_suspected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retrieved_at: Mapped[datetime] = _created()


class SeoContent(Base):
    __tablename__ = "seo_content"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="blog")
    target_keyword: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    meta_description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    slug: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    brief: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # planned / drafting / needs_revision / held_for_review / approved / scheduled / published / failed
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="planned", index=True)
    seo_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    guardrail_findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quality_findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    refresh_of_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    cms_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = _created()


class SeoTechnicalAudit(Base):
    __tablename__ = "seo_technical_audits"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    pages_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issue_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    health_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _created()


class SeoTechnicalIssue(Base):
    __tablename__ = "seo_technical_issues"
    __table_args__ = (UniqueConstraint("site_id", "fingerprint", name="uq_seo_issues_site_fingerprint"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    audit_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)  # speed/indexing/crawl/content/schema/redirect
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # critical/warning/info
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # open / awaiting_approval / planned / fixed / failed / dismissed / resolved
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", index=True)
    auto_fixable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk: Mapped[str] = mapped_column(String(8), nullable=False, default="low")
    fix_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fix_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_seen_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = _created()
    fixed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SeoGeoPrompt(Base):
    """A question people ask AI assistants that the brand should show up in."""

    __tablename__ = "seo_geo_prompts"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    prompt: Mapped[str] = mapped_column(String(500), nullable=False)
    cluster: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created()


class SeoGeoCheck(Base):
    __tablename__ = "seo_geo_checks"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    prompt_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("seo_geo_prompts.id"), nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    answer_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cited_urls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    cited_domains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    brand_mentioned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mention_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_cited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sentiment: Mapped[str] = mapped_column(String(16), nullable=False, default="neutral")
    competitors_mentioned: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    checked_at: Mapped[datetime] = _created()


class SeoLinkProspect(Base):
    __tablename__ = "seo_link_prospects"
    __table_args__ = (UniqueConstraint("site_id", "domain", name="uq_seo_prospects_site_domain"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    domain: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    link_type: Mapped[str] = mapped_column(String(24), nullable=False, default="editorial")
    relevance: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    authority: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    spam_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    # prospect / approved_for_outreach / contacted / replied / acquired / rejected / lost / blocked
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="prospect")
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _created()


class SeoOutreach(Base):
    __tablename__ = "seo_outreach"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    prospect_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("seo_link_prospects.id"), nullable=False, index=True)
    to_contact: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # queued / sent / failed / blocked_suppressed / blocked_guardrail
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    delivery_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = _created()


class SeoBacklink(Base):
    __tablename__ = "seo_backlinks"
    __table_args__ = (UniqueConstraint("site_id", "source_url", "target_url", name="uq_seo_backlinks_site_src_target"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    prospect_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_domain: Mapped[str] = mapped_column(String(256), nullable=False)
    target_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    anchor: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    rel: Mapped[str] = mapped_column(String(16), nullable=False, default="follow")
    authority_at_acquisition: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active / lost
    acquired_at: Mapped[datetime] = _created()
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SeoAuthoritySnapshot(Base):
    __tablename__ = "seo_authority_snapshots"
    __table_args__ = (UniqueConstraint("site_id", "on", name="uq_seo_authority_site_day"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    on: Mapped[date] = mapped_column(Date, nullable=False)
    authority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    referring_domains: Mapped[int | None] = mapped_column(Integer, nullable=True)
    backlinks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="")


class SeoCroFinding(Base):
    __tablename__ = "seo_cro_findings"
    __table_args__ = (UniqueConstraint("site_id", "page_url", "code", name="uq_seo_cro_site_page_code"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    page_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recommendation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # open / testing / resolved / dismissed
    created_at: Mapped[datetime] = _created()


class SeoCroExperiment(Base):
    __tablename__ = "seo_cro_experiments"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    finding_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    page_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    element: Mapped[str] = mapped_column(String(32), nullable=False, default="cta")
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    variant_a: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    variant_b: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    guardrail_findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running/concluded/inconclusive/held
    visitors_a: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversions_a: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    visitors_b: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversions_b: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    winner: Mapped[str | None] = mapped_column(String(1), nullable=True)
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = _created()
    concluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SeoPseoTemplate(Base):
    __tablename__ = "seo_pseo_templates"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    slug_pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    title_pattern: Mapped[str] = mapped_column(String(300), nullable=False)
    meta_pattern: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    required_slots: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    min_words: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    min_unique_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    created_at: Mapped[datetime] = _created()


class SeoPseoRow(Base):
    """One row of data a template turns into a page."""

    __tablename__ = "seo_pseo_rows"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    template_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("seo_pseo_templates.id"), nullable=False, index=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created()


class SeoPseoPage(Base):
    __tablename__ = "seo_pseo_pages"
    __table_args__ = (UniqueConstraint("site_id", "slug", name="uq_seo_pseo_pages_site_slug"),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    template_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("seo_pseo_templates.id"), nullable=False, index=True)
    row_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    meta_description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    words: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    max_similarity: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="approved")  # held/approved/published/failed
    hold_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    guardrail_findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    published_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    cms_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = _created()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SeoLocation(Base):
    """A physical location with a Google Business Profile."""

    __tablename__ = "seo_locations"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    address: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    website: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    categories: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    services: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    hours: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    gbp_location_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # description, photos_count, has_logo, has_cover, posts_last_7d,
    # qa_unanswered, reviews_count, reviews_avg, review_response_rate,
    # special_hours, pending (changes waiting to reach the profile)
    profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # [{"directory", "name", "street", "city", "postal", "phone"}]
    citations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    local_keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    gbp_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gbp_missing: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_audit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()


class SeoLocalRank(Base):
    __tablename__ = "seo_local_ranks"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    location_id: Mapped[uuid.UUID] = mapped_column(GUID, ForeignKey("seo_locations.id"), nullable=False, index=True)
    keyword: Mapped[str] = mapped_column(String(300), nullable=False)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_pack: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checked_at: Mapped[datetime] = _created()


class SeoReport(Base):
    __tablename__ = "seo_reports"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    narrative: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = _created()


class SeoTaskRequest(Base):
    """A task request emailed in by a person."""

    __tablename__ = "seo_task_requests"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # received / converted / needs_clarification / rejected_sender
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="received")
    injection_suspected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parsed: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    task_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    received_at: Mapped[datetime] = _created()


class SeoActivity(Base):
    """The live feed: everything a person or an agent did on a site."""

    __tablename__ = "seo_activity"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[str] = _tenant()
    site_id: Mapped[uuid.UUID] = _site()
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)
