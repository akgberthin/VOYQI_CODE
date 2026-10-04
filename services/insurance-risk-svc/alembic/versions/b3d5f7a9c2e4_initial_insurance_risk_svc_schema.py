"""initial insurance_risk_svc schema

Revision ID: b3d5f7a9c2e4
Revises:
Create Date: 2026-09-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "b3d5f7a9c2e4"
down_revision = None
branch_labels = None
depends_on = None

policy_status_enum = sa.Enum("active", "lapsed", "cancelled", name="policystatus")
risk_band_enum = sa.Enum("low", "elevated", "high", name="riskband")


def upgrade() -> None:
    op.create_table(
        "risk_policies",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("policy_number", sa.String(64), nullable=False),
        sa.Column("policy_type", sa.String(64), nullable=False),
        sa.Column("insured_name", sa.String(128), nullable=False),
        sa.Column("coverage_amount_usd", sa.Float(), nullable=False),
        sa.Column("status", policy_status_enum, nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_risk_policies_tenant_id", "risk_policies", ["tenant_id"])
    op.create_index("ix_risk_policies_policy_number", "risk_policies", ["policy_number"])

    op.create_table(
        "risk_assessments",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("policy_id", sa.CHAR(36), sa.ForeignKey("risk_policies.id"), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("risk_band", risk_band_enum, nullable=False),
        sa.Column("drivers", sa.JSON(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_risk_assessments_tenant_id", "risk_assessments", ["tenant_id"])
    op.create_index("ix_risk_assessments_policy_id", "risk_assessments", ["policy_id"])


def downgrade() -> None:
    op.drop_table("risk_assessments")
    op.drop_table("risk_policies")
    policy_status_enum.drop(op.get_bind(), checkfirst=True)
    risk_band_enum.drop(op.get_bind(), checkfirst=True)
