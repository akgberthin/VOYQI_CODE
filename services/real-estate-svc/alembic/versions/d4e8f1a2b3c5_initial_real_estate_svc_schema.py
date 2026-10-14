"""initial real_estate_svc schema

Revision ID: d4e8f1a2b3c5
Revises:
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "d4e8f1a2b3c5"
down_revision = None
branch_labels = None
depends_on = None

property_type_enum = sa.Enum("hub", "office", "warehouse", "other", name="propertytype")
property_status_enum = sa.Enum("active", "inactive", name="propertystatus")
metric_domain_enum = sa.Enum("occupancy", "lease_cost", "maintenance", "utilization", name="realestatemetricdomain")
comparison_enum = sa.Enum("gte", "lte", name="thresholdcomparison")
alert_status_enum = sa.Enum("open", "resolved", name="alertstatus")


def upgrade() -> None:
    op.create_table(
        "properties",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("property_type", property_type_enum, nullable=False),
        sa.Column("status", property_status_enum, nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_properties_tenant_id", "properties", ["tenant_id"])
    op.create_index("ix_properties_key", "properties", ["key"])

    op.create_table(
        "property_rollup_snapshots",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("property_key", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("source_service", sa.String(64), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_property_rollup_snapshots_tenant_id", "property_rollup_snapshots", ["tenant_id"])
    op.create_index("ix_property_rollup_snapshots_property_key", "property_rollup_snapshots", ["property_key"])
    op.create_index("ix_property_rollup_snapshots_metric_domain", "property_rollup_snapshots", ["metric_domain"])

    op.create_table(
        "property_kpi_thresholds",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("comparison", comparison_enum, nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_property_kpi_thresholds_tenant_id", "property_kpi_thresholds", ["tenant_id"])

    op.create_table(
        "property_kpi_alerts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("property_key", sa.String(64), nullable=False),
        sa.Column("metric_domain", metric_domain_enum, nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("comparison", comparison_enum, nullable=False),
        sa.Column("status", alert_status_enum, nullable=False, server_default="open"),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_property_kpi_alerts_tenant_id", "property_kpi_alerts", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("property_kpi_alerts")
    op.drop_table("property_kpi_thresholds")
    op.drop_table("property_rollup_snapshots")
    op.drop_table("properties")
    property_type_enum.drop(op.get_bind(), checkfirst=True)
    property_status_enum.drop(op.get_bind(), checkfirst=True)
    metric_domain_enum.drop(op.get_bind(), checkfirst=True)
    comparison_enum.drop(op.get_bind(), checkfirst=True)
    alert_status_enum.drop(op.get_bind(), checkfirst=True)
