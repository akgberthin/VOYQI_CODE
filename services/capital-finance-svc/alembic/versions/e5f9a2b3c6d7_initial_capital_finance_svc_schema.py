"""initial capital_finance_svc schema

Revision ID: e5f9a2b3c6d7
Revises:
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "e5f9a2b3c6d7"
down_revision = None
branch_labels = None
depends_on = None

source_type_enum = sa.Enum("debt", "equity", "credit_line", "other", name="fundingsourcetype")
source_status_enum = sa.Enum("active", "inactive", name="fundingsourcestatus")
metric_domain_enum = sa.Enum(
    "balance", "utilization", "cost_of_capital", "covenant_headroom", name="capitalmetricdomain"
)
comparison_enum = sa.Enum("gte", "lte", name="thresholdcomparison")
alert_status_enum = sa.Enum("open", "resolved", name="alertstatus")


def upgrade() -> None:
    op.create_table(
        "funding_sources",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("source_type", source_type_enum, nullable=False),
        sa.Column("status", source_status_enum, nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_funding_sources_tenant_id", "funding_sources", ["tenant_id"])
    op.create_index("ix_funding_sources_key", "funding_sources", ["key"])

    op.create_table(
        "capital_rollup_snapshots",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("funding_source_key", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("source_service", sa.String(64), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_capital_rollup_snapshots_tenant_id", "capital_rollup_snapshots", ["tenant_id"])
    op.create_index(
        "ix_capital_rollup_snapshots_funding_source_key", "capital_rollup_snapshots", ["funding_source_key"]
    )
    op.create_index("ix_capital_rollup_snapshots_metric_domain", "capital_rollup_snapshots", ["metric_domain"])

    op.create_table(
        "capital_kpi_thresholds",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("comparison", comparison_enum, nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_capital_kpi_thresholds_tenant_id", "capital_kpi_thresholds", ["tenant_id"])

    op.create_table(
        "capital_kpi_alerts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("funding_source_key", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("comparison", comparison_enum, nullable=False),
        sa.Column("status", alert_status_enum, nullable=False, server_default="open"),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_capital_kpi_alerts_tenant_id", "capital_kpi_alerts", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("capital_kpi_alerts")
    op.drop_table("capital_kpi_thresholds")
    op.drop_table("capital_rollup_snapshots")
    op.drop_table("funding_sources")
    source_type_enum.drop(op.get_bind(), checkfirst=True)
    source_status_enum.drop(op.get_bind(), checkfirst=True)
    metric_domain_enum.drop(op.get_bind(), checkfirst=True)
    comparison_enum.drop(op.get_bind(), checkfirst=True)
    alert_status_enum.drop(op.get_bind(), checkfirst=True)
