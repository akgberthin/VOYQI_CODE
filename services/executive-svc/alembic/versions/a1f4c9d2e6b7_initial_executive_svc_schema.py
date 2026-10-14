"""initial executive_svc schema

Revision ID: a1f4c9d2e6b7
Revises:
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1f4c9d2e6b7"
down_revision = None
branch_labels = None
depends_on = None

metric_domain_enum = sa.Enum("revenue", "fleet", "workforce", "marketing", name="metricdomain")
comparison_enum = sa.Enum("gte", "lte", name="thresholdcomparison")
alert_status_enum = sa.Enum("open", "resolved", name="alertstatus")


def upgrade() -> None:
    op.create_table(
        "rollup_snapshots",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("subsidiary_key", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("source_service", sa.String(64), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rollup_snapshots_tenant_id", "rollup_snapshots", ["tenant_id"])
    op.create_index("ix_rollup_snapshots_subsidiary_key", "rollup_snapshots", ["subsidiary_key"])
    op.create_index("ix_rollup_snapshots_metric_domain", "rollup_snapshots", ["metric_domain"])

    op.create_table(
        "kpi_thresholds",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("comparison", comparison_enum, nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_kpi_thresholds_tenant_id", "kpi_thresholds", ["tenant_id"])

    op.create_table(
        "kpi_alerts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("subsidiary_key", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("comparison", comparison_enum, nullable=False),
        sa.Column("status", alert_status_enum, nullable=False, server_default="open"),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_kpi_alerts_tenant_id", "kpi_alerts", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("kpi_alerts")
    op.drop_table("kpi_thresholds")
    op.drop_table("rollup_snapshots")
    metric_domain_enum.drop(op.get_bind(), checkfirst=True)
    comparison_enum.drop(op.get_bind(), checkfirst=True)
    alert_status_enum.drop(op.get_bind(), checkfirst=True)
