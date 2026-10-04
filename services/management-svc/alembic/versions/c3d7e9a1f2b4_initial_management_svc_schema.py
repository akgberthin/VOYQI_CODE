"""initial management_svc schema

Revision ID: c3d7e9a1f2b4
Revises:
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c3d7e9a1f2b4"
down_revision = None
branch_labels = None
depends_on = None

category_enum = sa.Enum("it", "facilities", "procurement", "shared_staff", "other", name="costcentercategory")
cc_status_enum = sa.Enum("active", "inactive", name="costcenterstatus")
metric_domain_enum = sa.Enum("spend", "headcount", "utilization", "sla", name="opsmetricdomain")
comparison_enum = sa.Enum("gte", "lte", name="thresholdcomparison")
alert_status_enum = sa.Enum("open", "resolved", name="alertstatus")


def upgrade() -> None:
    op.create_table(
        "cost_centers",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("status", cc_status_enum, nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_cost_centers_tenant_id", "cost_centers", ["tenant_id"])
    op.create_index("ix_cost_centers_key", "cost_centers", ["key"])

    op.create_table(
        "ops_rollup_snapshots",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("cost_center_key", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("source_service", sa.String(64), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ops_rollup_snapshots_tenant_id", "ops_rollup_snapshots", ["tenant_id"])
    op.create_index("ix_ops_rollup_snapshots_cost_center_key", "ops_rollup_snapshots", ["cost_center_key"])
    op.create_index("ix_ops_rollup_snapshots_metric_domain", "ops_rollup_snapshots", ["metric_domain"])

    op.create_table(
        "ops_kpi_thresholds",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("comparison", comparison_enum, nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ops_kpi_thresholds_tenant_id", "ops_kpi_thresholds", ["tenant_id"])

    op.create_table(
        "ops_kpi_alerts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("cost_center_key", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("comparison", comparison_enum, nullable=False),
        sa.Column("status", alert_status_enum, nullable=False, server_default="open"),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ops_kpi_alerts_tenant_id", "ops_kpi_alerts", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("ops_kpi_alerts")
    op.drop_table("ops_kpi_thresholds")
    op.drop_table("ops_rollup_snapshots")
    op.drop_table("cost_centers")
    category_enum.drop(op.get_bind(), checkfirst=True)
    cc_status_enum.drop(op.get_bind(), checkfirst=True)
    metric_domain_enum.drop(op.get_bind(), checkfirst=True)
    comparison_enum.drop(op.get_bind(), checkfirst=True)
    alert_status_enum.drop(op.get_bind(), checkfirst=True)
