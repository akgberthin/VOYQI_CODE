"""growth / SEO engine: sites, integrations, the agent task queue,
strategy, keywords, research, content, technical issues, AI-answer
checks, links, conversion, programmatic pages, local profiles, reports

Revision ID: f2a6c8e1b3d5
Revises: e9b3d5f7a1c4
Create Date: 2026-10-27
"""
import sqlalchemy as sa
from alembic import op

import src.core.database

revision = "f2a6c8e1b3d5"
down_revision = "e9b3d5f7a1c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('seo_sites',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('domain', sa.String(length=256), nullable=False),
    sa.Column('business_profile', sa.JSON(), nullable=False),
    sa.Column('goals', sa.JSON(), nullable=False),
    sa.Column('autopilot_mode', sa.String(length=16), nullable=False),
    sa.Column('autonomy_overrides', sa.JSON(), nullable=False),
    sa.Column('caps', sa.JSON(), nullable=False),
    sa.Column('report_frequency', sa.String(length=16), nullable=False),
    sa.Column('report_recipients', sa.JSON(), nullable=False),
    sa.Column('authorized_requesters', sa.JSON(), nullable=False),
    sa.Column('last_cycle_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'domain', name='uq_seo_sites_tenant_domain')
    )
    op.create_index(op.f('ix_seo_sites_tenant_id'), 'seo_sites', ['tenant_id'], unique=False)
    op.create_table('seo_activity',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('ref_type', sa.String(length=32), nullable=True),
    sa.Column('ref_id', sa.String(length=64), nullable=True),
    sa.Column('actor', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_activity_created_at'), 'seo_activity', ['created_at'], unique=False)
    op.create_index(op.f('ix_seo_activity_kind'), 'seo_activity', ['kind'], unique=False)
    op.create_index(op.f('ix_seo_activity_site_id'), 'seo_activity', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_activity_tenant_id'), 'seo_activity', ['tenant_id'], unique=False)
    op.create_table('seo_authority_snapshots',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('on', sa.Date(), nullable=False),
    sa.Column('authority_score', sa.Float(), nullable=True),
    sa.Column('referring_domains', sa.Integer(), nullable=True),
    sa.Column('backlinks', sa.Integer(), nullable=True),
    sa.Column('source', sa.String(length=128), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'on', name='uq_seo_authority_site_day')
    )
    op.create_index(op.f('ix_seo_authority_snapshots_site_id'), 'seo_authority_snapshots', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_authority_snapshots_tenant_id'), 'seo_authority_snapshots', ['tenant_id'], unique=False)
    op.create_table('seo_backlinks',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('prospect_id', src.core.database.GUID(), nullable=True),
    sa.Column('source_url', sa.String(length=1024), nullable=False),
    sa.Column('source_domain', sa.String(length=256), nullable=False),
    sa.Column('target_url', sa.String(length=1024), nullable=False),
    sa.Column('anchor', sa.String(length=512), nullable=False),
    sa.Column('rel', sa.String(length=16), nullable=False),
    sa.Column('authority_at_acquisition', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('acquired_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lost_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'source_url', 'target_url', name='uq_seo_backlinks_site_src_target')
    )
    op.create_index(op.f('ix_seo_backlinks_site_id'), 'seo_backlinks', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_backlinks_tenant_id'), 'seo_backlinks', ['tenant_id'], unique=False)
    op.create_table('seo_competitors',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('domain', sa.String(length=256), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('authority_score', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'domain', name='uq_seo_competitors_site_domain')
    )
    op.create_index(op.f('ix_seo_competitors_site_id'), 'seo_competitors', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_competitors_tenant_id'), 'seo_competitors', ['tenant_id'], unique=False)
    op.create_table('seo_content',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('target_keyword', sa.String(length=300), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('meta_description', sa.String(length=512), nullable=False),
    sa.Column('slug', sa.String(length=160), nullable=False),
    sa.Column('body_markdown', sa.Text(), nullable=False),
    sa.Column('brief', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('seo_score', sa.Integer(), nullable=False),
    sa.Column('guardrail_findings', sa.JSON(), nullable=False),
    sa.Column('quality_findings', sa.JSON(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('refresh_of_url', sa.String(length=1024), nullable=True),
    sa.Column('publish_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('published_url', sa.String(length=1024), nullable=True),
    sa.Column('cms_ref', sa.String(length=256), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.String(length=128), nullable=True),
    sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_content_site_id'), 'seo_content', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_content_status'), 'seo_content', ['status'], unique=False)
    op.create_index(op.f('ix_seo_content_tenant_id'), 'seo_content', ['tenant_id'], unique=False)
    op.create_table('seo_cro_experiments',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('finding_id', src.core.database.GUID(), nullable=True),
    sa.Column('page_url', sa.String(length=1024), nullable=False),
    sa.Column('element', sa.String(length=32), nullable=False),
    sa.Column('hypothesis', sa.Text(), nullable=False),
    sa.Column('variant_a', sa.JSON(), nullable=False),
    sa.Column('variant_b', sa.JSON(), nullable=False),
    sa.Column('guardrail_findings', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('visitors_a', sa.Integer(), nullable=False),
    sa.Column('conversions_a', sa.Integer(), nullable=False),
    sa.Column('visitors_b', sa.Integer(), nullable=False),
    sa.Column('conversions_b', sa.Integer(), nullable=False),
    sa.Column('winner', sa.String(length=1), nullable=True),
    sa.Column('p_value', sa.Float(), nullable=True),
    sa.Column('created_by', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('concluded_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_cro_experiments_site_id'), 'seo_cro_experiments', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_cro_experiments_tenant_id'), 'seo_cro_experiments', ['tenant_id'], unique=False)
    op.create_table('seo_cro_findings',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('page_url', sa.String(length=1024), nullable=False),
    sa.Column('code', sa.String(length=48), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('recommendation', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'page_url', 'code', name='uq_seo_cro_site_page_code')
    )
    op.create_index(op.f('ix_seo_cro_findings_site_id'), 'seo_cro_findings', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_cro_findings_tenant_id'), 'seo_cro_findings', ['tenant_id'], unique=False)
    op.create_table('seo_geo_prompts',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('prompt', sa.String(length=500), nullable=False),
    sa.Column('cluster', sa.String(length=300), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_geo_prompts_site_id'), 'seo_geo_prompts', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_geo_prompts_tenant_id'), 'seo_geo_prompts', ['tenant_id'], unique=False)
    op.create_table('seo_integrations',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=True),
    sa.Column('credential_ref', sa.String(length=256), nullable=True),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_tested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'kind', name='uq_seo_integrations_site_kind')
    )
    op.create_index(op.f('ix_seo_integrations_site_id'), 'seo_integrations', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_integrations_tenant_id'), 'seo_integrations', ['tenant_id'], unique=False)
    op.create_table('seo_keywords',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('term', sa.String(length=300), nullable=False),
    sa.Column('owner', sa.String(length=256), nullable=False),
    sa.Column('volume', sa.Integer(), nullable=True),
    sa.Column('difficulty', sa.Integer(), nullable=True),
    sa.Column('cpc_cents', sa.Integer(), nullable=True),
    sa.Column('position', sa.Integer(), nullable=True),
    sa.Column('url', sa.String(length=1024), nullable=True),
    sa.Column('intent', sa.String(length=16), nullable=False),
    sa.Column('purchase_intent', sa.Integer(), nullable=False),
    sa.Column('opportunity', sa.Float(), nullable=False),
    sa.Column('cluster', sa.String(length=300), nullable=False),
    sa.Column('trend', sa.String(length=16), nullable=False),
    sa.Column('growth_pct', sa.Float(), nullable=True),
    sa.Column('is_question', sa.Boolean(), nullable=False),
    sa.Column('is_gap', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=128), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'term', 'owner', name='uq_seo_keywords_site_term_owner')
    )
    op.create_index(op.f('ix_seo_keywords_site_id'), 'seo_keywords', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_keywords_tenant_id'), 'seo_keywords', ['tenant_id'], unique=False)
    op.create_table('seo_link_prospects',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('domain', sa.String(length=256), nullable=False),
    sa.Column('url', sa.String(length=1024), nullable=True),
    sa.Column('contact_email', sa.String(length=320), nullable=True),
    sa.Column('link_type', sa.String(length=24), nullable=False),
    sa.Column('relevance', sa.Float(), nullable=False),
    sa.Column('authority', sa.Float(), nullable=False),
    sa.Column('spam_score', sa.Float(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('block_reason', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'domain', name='uq_seo_prospects_site_domain')
    )
    op.create_index(op.f('ix_seo_link_prospects_site_id'), 'seo_link_prospects', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_link_prospects_tenant_id'), 'seo_link_prospects', ['tenant_id'], unique=False)
    op.create_table('seo_locations',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('address', sa.JSON(), nullable=False),
    sa.Column('phone', sa.String(length=32), nullable=False),
    sa.Column('website', sa.String(length=1024), nullable=False),
    sa.Column('categories', sa.JSON(), nullable=False),
    sa.Column('services', sa.JSON(), nullable=False),
    sa.Column('hours', sa.JSON(), nullable=False),
    sa.Column('gbp_location_id', sa.String(length=256), nullable=True),
    sa.Column('profile', sa.JSON(), nullable=False),
    sa.Column('citations', sa.JSON(), nullable=False),
    sa.Column('local_keywords', sa.JSON(), nullable=False),
    sa.Column('gbp_score', sa.Integer(), nullable=False),
    sa.Column('gbp_missing', sa.JSON(), nullable=False),
    sa.Column('last_audit_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_locations_site_id'), 'seo_locations', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_locations_tenant_id'), 'seo_locations', ['tenant_id'], unique=False)
    op.create_table('seo_organic_daily',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('clicks', sa.Integer(), nullable=False),
    sa.Column('impressions', sa.Integer(), nullable=False),
    sa.Column('position', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'day', name='uq_seo_organic_site_day')
    )
    op.create_index(op.f('ix_seo_organic_daily_site_id'), 'seo_organic_daily', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_organic_daily_tenant_id'), 'seo_organic_daily', ['tenant_id'], unique=False)
    op.create_table('seo_page_stats',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('url', sa.String(length=1024), nullable=False),
    sa.Column('window', sa.String(length=16), nullable=False),
    sa.Column('clicks', sa.Integer(), nullable=False),
    sa.Column('impressions', sa.Integer(), nullable=False),
    sa.Column('position', sa.Float(), nullable=True),
    sa.Column('top_query', sa.String(length=300), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'url', 'window', name='uq_seo_page_stats_site_url_window')
    )
    op.create_index(op.f('ix_seo_page_stats_site_id'), 'seo_page_stats', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_page_stats_tenant_id'), 'seo_page_stats', ['tenant_id'], unique=False)
    op.create_table('seo_pseo_templates',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('name', sa.String(length=256), nullable=False),
    sa.Column('slug_pattern', sa.String(length=256), nullable=False),
    sa.Column('title_pattern', sa.String(length=300), nullable=False),
    sa.Column('meta_pattern', sa.String(length=512), nullable=False),
    sa.Column('body_template', sa.Text(), nullable=False),
    sa.Column('required_slots', sa.JSON(), nullable=False),
    sa.Column('min_words', sa.Integer(), nullable=False),
    sa.Column('min_unique_ratio', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_pseo_templates_site_id'), 'seo_pseo_templates', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_pseo_templates_tenant_id'), 'seo_pseo_templates', ['tenant_id'], unique=False)
    op.create_table('seo_rank_snapshots',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('keyword', sa.String(length=300), nullable=False),
    sa.Column('on', sa.Date(), nullable=False),
    sa.Column('position', sa.Float(), nullable=True),
    sa.Column('url', sa.String(length=1024), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'keyword', 'on', 'source', name='uq_seo_rank_site_kw_day_src')
    )
    op.create_index(op.f('ix_seo_rank_snapshots_site_id'), 'seo_rank_snapshots', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_rank_snapshots_tenant_id'), 'seo_rank_snapshots', ['tenant_id'], unique=False)
    op.create_table('seo_reports',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('period', sa.String(length=16), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=False),
    sa.Column('narrative', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_reports_site_id'), 'seo_reports', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_reports_tenant_id'), 'seo_reports', ['tenant_id'], unique=False)
    op.create_table('seo_research_items',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('source', sa.String(length=24), nullable=False),
    sa.Column('engine', sa.String(length=32), nullable=True),
    sa.Column('query', sa.String(length=300), nullable=False),
    sa.Column('title', sa.String(length=512), nullable=False),
    sa.Column('url', sa.String(length=1024), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('signals', sa.JSON(), nullable=False),
    sa.Column('injection_suspected', sa.Boolean(), nullable=False),
    sa.Column('retrieved_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'source', 'url', name='uq_seo_research_site_source_url')
    )
    op.create_index(op.f('ix_seo_research_items_site_id'), 'seo_research_items', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_research_items_tenant_id'), 'seo_research_items', ['tenant_id'], unique=False)
    op.create_table('seo_strategies',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('narrative', sa.Text(), nullable=False),
    sa.Column('initiatives', sa.JSON(), nullable=False),
    sa.Column('inputs_snapshot', sa.JSON(), nullable=False),
    sa.Column('generated_by', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_strategies_site_id'), 'seo_strategies', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_strategies_tenant_id'), 'seo_strategies', ['tenant_id'], unique=False)
    op.create_table('seo_task_requests',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('sender', sa.String(length=320), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('injection_suspected', sa.Boolean(), nullable=False),
    sa.Column('parsed', sa.JSON(), nullable=False),
    sa.Column('task_ids', sa.JSON(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_task_requests_site_id'), 'seo_task_requests', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_task_requests_tenant_id'), 'seo_task_requests', ['tenant_id'], unique=False)
    op.create_table('seo_tasks',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('impact', sa.Float(), nullable=False),
    sa.Column('effort', sa.Float(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('priority', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('owning_agent', sa.String(length=64), nullable=False),
    sa.Column('requires_approval', sa.Boolean(), nullable=False),
    sa.Column('approval_reason', sa.Text(), nullable=True),
    sa.Column('approved_by', sa.String(length=128), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('strategy_id', src.core.database.GUID(), nullable=True),
    sa.Column('created_by', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_tasks_site_id'), 'seo_tasks', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_tasks_status'), 'seo_tasks', ['status'], unique=False)
    op.create_index(op.f('ix_seo_tasks_tenant_id'), 'seo_tasks', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_seo_tasks_type'), 'seo_tasks', ['type'], unique=False)
    op.create_table('seo_technical_audits',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('source', sa.String(length=128), nullable=False),
    sa.Column('pages_scanned', sa.Integer(), nullable=False),
    sa.Column('issue_counts', sa.JSON(), nullable=False),
    sa.Column('health_score', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_technical_audits_site_id'), 'seo_technical_audits', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_technical_audits_tenant_id'), 'seo_technical_audits', ['tenant_id'], unique=False)
    op.create_table('seo_technical_issues',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('audit_id', src.core.database.GUID(), nullable=True),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('category', sa.String(length=24), nullable=False),
    sa.Column('code', sa.String(length=48), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('url', sa.String(length=1024), nullable=False),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('auto_fixable', sa.Boolean(), nullable=False),
    sa.Column('risk', sa.String(length=8), nullable=False),
    sa.Column('fix_plan', sa.JSON(), nullable=True),
    sa.Column('fix_result', sa.JSON(), nullable=True),
    sa.Column('approved_by', sa.String(length=128), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('fixed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'fingerprint', name='uq_seo_issues_site_fingerprint')
    )
    op.create_index(op.f('ix_seo_technical_issues_site_id'), 'seo_technical_issues', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_technical_issues_status'), 'seo_technical_issues', ['status'], unique=False)
    op.create_index(op.f('ix_seo_technical_issues_tenant_id'), 'seo_technical_issues', ['tenant_id'], unique=False)
    op.create_table('seo_traffic_daily',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('sessions', sa.Integer(), nullable=False),
    sa.Column('organic_sessions', sa.Integer(), nullable=False),
    sa.Column('conversions', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'day', name='uq_seo_traffic_site_day')
    )
    op.create_index(op.f('ix_seo_traffic_daily_site_id'), 'seo_traffic_daily', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_traffic_daily_tenant_id'), 'seo_traffic_daily', ['tenant_id'], unique=False)
    op.create_table('seo_geo_checks',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('prompt_id', src.core.database.GUID(), nullable=False),
    sa.Column('engine', sa.String(length=32), nullable=False),
    sa.Column('answer_excerpt', sa.Text(), nullable=False),
    sa.Column('cited_urls', sa.JSON(), nullable=False),
    sa.Column('cited_domains', sa.JSON(), nullable=False),
    sa.Column('brand_mentioned', sa.Boolean(), nullable=False),
    sa.Column('mention_position', sa.Integer(), nullable=True),
    sa.Column('domain_cited', sa.Boolean(), nullable=False),
    sa.Column('sentiment', sa.String(length=16), nullable=False),
    sa.Column('competitors_mentioned', sa.JSON(), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['prompt_id'], ['seo_geo_prompts.id'], ),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_geo_checks_prompt_id'), 'seo_geo_checks', ['prompt_id'], unique=False)
    op.create_index(op.f('ix_seo_geo_checks_site_id'), 'seo_geo_checks', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_geo_checks_tenant_id'), 'seo_geo_checks', ['tenant_id'], unique=False)
    op.create_table('seo_local_ranks',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('location_id', src.core.database.GUID(), nullable=False),
    sa.Column('keyword', sa.String(length=300), nullable=False),
    sa.Column('position', sa.Integer(), nullable=True),
    sa.Column('in_pack', sa.Boolean(), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['location_id'], ['seo_locations.id'], ),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_local_ranks_location_id'), 'seo_local_ranks', ['location_id'], unique=False)
    op.create_index(op.f('ix_seo_local_ranks_site_id'), 'seo_local_ranks', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_local_ranks_tenant_id'), 'seo_local_ranks', ['tenant_id'], unique=False)
    op.create_table('seo_outreach',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('prospect_id', src.core.database.GUID(), nullable=False),
    sa.Column('to_contact', sa.String(length=320), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('delivery_mode', sa.String(length=16), nullable=True),
    sa.Column('findings', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['prospect_id'], ['seo_link_prospects.id'], ),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_outreach_prospect_id'), 'seo_outreach', ['prospect_id'], unique=False)
    op.create_index(op.f('ix_seo_outreach_site_id'), 'seo_outreach', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_outreach_tenant_id'), 'seo_outreach', ['tenant_id'], unique=False)
    op.create_table('seo_pseo_pages',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('template_id', src.core.database.GUID(), nullable=False),
    sa.Column('row_id', src.core.database.GUID(), nullable=True),
    sa.Column('slug', sa.String(length=160), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('meta_description', sa.String(length=512), nullable=False),
    sa.Column('body_markdown', sa.Text(), nullable=False),
    sa.Column('words', sa.Integer(), nullable=False),
    sa.Column('unique_ratio', sa.Float(), nullable=False),
    sa.Column('max_similarity', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('hold_reasons', sa.JSON(), nullable=False),
    sa.Column('guardrail_findings', sa.JSON(), nullable=False),
    sa.Column('published_url', sa.String(length=1024), nullable=True),
    sa.Column('cms_ref', sa.String(length=256), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.ForeignKeyConstraint(['template_id'], ['seo_pseo_templates.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'slug', name='uq_seo_pseo_pages_site_slug')
    )
    op.create_index(op.f('ix_seo_pseo_pages_site_id'), 'seo_pseo_pages', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_pseo_pages_template_id'), 'seo_pseo_pages', ['template_id'], unique=False)
    op.create_index(op.f('ix_seo_pseo_pages_tenant_id'), 'seo_pseo_pages', ['tenant_id'], unique=False)
    op.create_table('seo_pseo_rows',
    sa.Column('id', src.core.database.GUID(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('site_id', src.core.database.GUID(), nullable=False),
    sa.Column('template_id', src.core.database.GUID(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('generated', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['seo_sites.id'], ),
    sa.ForeignKeyConstraint(['template_id'], ['seo_pseo_templates.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_seo_pseo_rows_site_id'), 'seo_pseo_rows', ['site_id'], unique=False)
    op.create_index(op.f('ix_seo_pseo_rows_template_id'), 'seo_pseo_rows', ['template_id'], unique=False)
    op.create_index(op.f('ix_seo_pseo_rows_tenant_id'), 'seo_pseo_rows', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_seo_pseo_rows_tenant_id'), table_name='seo_pseo_rows')
    op.drop_index(op.f('ix_seo_pseo_rows_template_id'), table_name='seo_pseo_rows')
    op.drop_index(op.f('ix_seo_pseo_rows_site_id'), table_name='seo_pseo_rows')
    op.drop_table('seo_pseo_rows')
    op.drop_index(op.f('ix_seo_pseo_pages_tenant_id'), table_name='seo_pseo_pages')
    op.drop_index(op.f('ix_seo_pseo_pages_template_id'), table_name='seo_pseo_pages')
    op.drop_index(op.f('ix_seo_pseo_pages_site_id'), table_name='seo_pseo_pages')
    op.drop_table('seo_pseo_pages')
    op.drop_index(op.f('ix_seo_outreach_tenant_id'), table_name='seo_outreach')
    op.drop_index(op.f('ix_seo_outreach_site_id'), table_name='seo_outreach')
    op.drop_index(op.f('ix_seo_outreach_prospect_id'), table_name='seo_outreach')
    op.drop_table('seo_outreach')
    op.drop_index(op.f('ix_seo_local_ranks_tenant_id'), table_name='seo_local_ranks')
    op.drop_index(op.f('ix_seo_local_ranks_site_id'), table_name='seo_local_ranks')
    op.drop_index(op.f('ix_seo_local_ranks_location_id'), table_name='seo_local_ranks')
    op.drop_table('seo_local_ranks')
    op.drop_index(op.f('ix_seo_geo_checks_tenant_id'), table_name='seo_geo_checks')
    op.drop_index(op.f('ix_seo_geo_checks_site_id'), table_name='seo_geo_checks')
    op.drop_index(op.f('ix_seo_geo_checks_prompt_id'), table_name='seo_geo_checks')
    op.drop_table('seo_geo_checks')
    op.drop_index(op.f('ix_seo_traffic_daily_tenant_id'), table_name='seo_traffic_daily')
    op.drop_index(op.f('ix_seo_traffic_daily_site_id'), table_name='seo_traffic_daily')
    op.drop_table('seo_traffic_daily')
    op.drop_index(op.f('ix_seo_technical_issues_tenant_id'), table_name='seo_technical_issues')
    op.drop_index(op.f('ix_seo_technical_issues_status'), table_name='seo_technical_issues')
    op.drop_index(op.f('ix_seo_technical_issues_site_id'), table_name='seo_technical_issues')
    op.drop_table('seo_technical_issues')
    op.drop_index(op.f('ix_seo_technical_audits_tenant_id'), table_name='seo_technical_audits')
    op.drop_index(op.f('ix_seo_technical_audits_site_id'), table_name='seo_technical_audits')
    op.drop_table('seo_technical_audits')
    op.drop_index(op.f('ix_seo_tasks_type'), table_name='seo_tasks')
    op.drop_index(op.f('ix_seo_tasks_tenant_id'), table_name='seo_tasks')
    op.drop_index(op.f('ix_seo_tasks_status'), table_name='seo_tasks')
    op.drop_index(op.f('ix_seo_tasks_site_id'), table_name='seo_tasks')
    op.drop_table('seo_tasks')
    op.drop_index(op.f('ix_seo_task_requests_tenant_id'), table_name='seo_task_requests')
    op.drop_index(op.f('ix_seo_task_requests_site_id'), table_name='seo_task_requests')
    op.drop_table('seo_task_requests')
    op.drop_index(op.f('ix_seo_strategies_tenant_id'), table_name='seo_strategies')
    op.drop_index(op.f('ix_seo_strategies_site_id'), table_name='seo_strategies')
    op.drop_table('seo_strategies')
    op.drop_index(op.f('ix_seo_research_items_tenant_id'), table_name='seo_research_items')
    op.drop_index(op.f('ix_seo_research_items_site_id'), table_name='seo_research_items')
    op.drop_table('seo_research_items')
    op.drop_index(op.f('ix_seo_reports_tenant_id'), table_name='seo_reports')
    op.drop_index(op.f('ix_seo_reports_site_id'), table_name='seo_reports')
    op.drop_table('seo_reports')
    op.drop_index(op.f('ix_seo_rank_snapshots_tenant_id'), table_name='seo_rank_snapshots')
    op.drop_index(op.f('ix_seo_rank_snapshots_site_id'), table_name='seo_rank_snapshots')
    op.drop_table('seo_rank_snapshots')
    op.drop_index(op.f('ix_seo_pseo_templates_tenant_id'), table_name='seo_pseo_templates')
    op.drop_index(op.f('ix_seo_pseo_templates_site_id'), table_name='seo_pseo_templates')
    op.drop_table('seo_pseo_templates')
    op.drop_index(op.f('ix_seo_page_stats_tenant_id'), table_name='seo_page_stats')
    op.drop_index(op.f('ix_seo_page_stats_site_id'), table_name='seo_page_stats')
    op.drop_table('seo_page_stats')
    op.drop_index(op.f('ix_seo_organic_daily_tenant_id'), table_name='seo_organic_daily')
    op.drop_index(op.f('ix_seo_organic_daily_site_id'), table_name='seo_organic_daily')
    op.drop_table('seo_organic_daily')
    op.drop_index(op.f('ix_seo_locations_tenant_id'), table_name='seo_locations')
    op.drop_index(op.f('ix_seo_locations_site_id'), table_name='seo_locations')
    op.drop_table('seo_locations')
    op.drop_index(op.f('ix_seo_link_prospects_tenant_id'), table_name='seo_link_prospects')
    op.drop_index(op.f('ix_seo_link_prospects_site_id'), table_name='seo_link_prospects')
    op.drop_table('seo_link_prospects')
    op.drop_index(op.f('ix_seo_keywords_tenant_id'), table_name='seo_keywords')
    op.drop_index(op.f('ix_seo_keywords_site_id'), table_name='seo_keywords')
    op.drop_table('seo_keywords')
    op.drop_index(op.f('ix_seo_integrations_tenant_id'), table_name='seo_integrations')
    op.drop_index(op.f('ix_seo_integrations_site_id'), table_name='seo_integrations')
    op.drop_table('seo_integrations')
    op.drop_index(op.f('ix_seo_geo_prompts_tenant_id'), table_name='seo_geo_prompts')
    op.drop_index(op.f('ix_seo_geo_prompts_site_id'), table_name='seo_geo_prompts')
    op.drop_table('seo_geo_prompts')
    op.drop_index(op.f('ix_seo_cro_findings_tenant_id'), table_name='seo_cro_findings')
    op.drop_index(op.f('ix_seo_cro_findings_site_id'), table_name='seo_cro_findings')
    op.drop_table('seo_cro_findings')
    op.drop_index(op.f('ix_seo_cro_experiments_tenant_id'), table_name='seo_cro_experiments')
    op.drop_index(op.f('ix_seo_cro_experiments_site_id'), table_name='seo_cro_experiments')
    op.drop_table('seo_cro_experiments')
    op.drop_index(op.f('ix_seo_content_tenant_id'), table_name='seo_content')
    op.drop_index(op.f('ix_seo_content_status'), table_name='seo_content')
    op.drop_index(op.f('ix_seo_content_site_id'), table_name='seo_content')
    op.drop_table('seo_content')
    op.drop_index(op.f('ix_seo_competitors_tenant_id'), table_name='seo_competitors')
    op.drop_index(op.f('ix_seo_competitors_site_id'), table_name='seo_competitors')
    op.drop_table('seo_competitors')
    op.drop_index(op.f('ix_seo_backlinks_tenant_id'), table_name='seo_backlinks')
    op.drop_index(op.f('ix_seo_backlinks_site_id'), table_name='seo_backlinks')
    op.drop_table('seo_backlinks')
    op.drop_index(op.f('ix_seo_authority_snapshots_tenant_id'), table_name='seo_authority_snapshots')
    op.drop_index(op.f('ix_seo_authority_snapshots_site_id'), table_name='seo_authority_snapshots')
    op.drop_table('seo_authority_snapshots')
    op.drop_index(op.f('ix_seo_activity_tenant_id'), table_name='seo_activity')
    op.drop_index(op.f('ix_seo_activity_site_id'), table_name='seo_activity')
    op.drop_index(op.f('ix_seo_activity_kind'), table_name='seo_activity')
    op.drop_index(op.f('ix_seo_activity_created_at'), table_name='seo_activity')
    op.drop_table('seo_activity')
    op.drop_index(op.f('ix_seo_sites_tenant_id'), table_name='seo_sites')
    op.drop_table('seo_sites')
