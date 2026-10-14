"""initial security_compliance_svc schema

Revision ID: c4e6a8b1d3f5
Revises:
Create Date: 2026-09-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "c4e6a8b1d3f5"
down_revision = None
branch_labels = None
depends_on = None

policy_status_enum = sa.Enum("active", "inactive", name="policystatus")
subject_type_enum = sa.Enum("user", "role", name="subjecttype")
grant_status_enum = sa.Enum("pending", "approved", "rejected", name="grantrequeststatus")


def upgrade() -> None:
    op.create_table(
        "compliance_policies",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), nullable=False),
        sa.Column("status", policy_status_enum, nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_compliance_policies_tenant_id", "compliance_policies", ["tenant_id"])
    op.create_index("ix_compliance_policies_key", "compliance_policies", ["key"])

    op.create_table(
        "policy_grant_requests",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("policy_id", sa.CHAR(36), sa.ForeignKey("compliance_policies.id"), nullable=False),
        sa.Column("subject_type", subject_type_enum, nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("simulation_result", sa.JSON(), nullable=True),
        sa.Column("status", grant_status_enum, nullable=False, server_default="pending"),
        sa.Column("accountable_role", sa.String(128), nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_policy_grant_requests_tenant_id", "policy_grant_requests", ["tenant_id"])
    op.create_index("ix_policy_grant_requests_policy_id", "policy_grant_requests", ["policy_id"])


def downgrade() -> None:
    op.drop_table("policy_grant_requests")
    op.drop_table("compliance_policies")
    policy_status_enum.drop(op.get_bind(), checkfirst=True)
    subject_type_enum.drop(op.get_bind(), checkfirst=True)
    grant_status_enum.drop(op.get_bind(), checkfirst=True)
