"""add ORG-001..003 origination pipeline (cases, decisions, funding sources)

Revision ID: e8a3c5d7f9b1
Revises: d5f7a9c1b3e5
Create Date: 2026-10-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "e8a3c5d7f9b1"
down_revision = "d5f7a9c1b3e5"
branch_labels = None
depends_on = None

origination_type_enum = sa.Enum("subscription_enrollment", "rental_conversion", name="originationtype")
case_status_enum = sa.Enum(
    "open", "prequalified", "needs_review", "incomplete", "in_decisioning", "approved", "declined",
    name="originationcasestatus",
)
org_decision_status_enum = sa.Enum("awaiting_score", "scored", "approved", "declined", name="orgdecisionstatus")
recommended_outcome_enum = sa.Enum("approve", "decline", "manual_review", name="recommendedoutcome")


def upgrade() -> None:
    op.create_table(
        "origination_cases",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("origination_type", origination_type_enum, nullable=False),
        sa.Column("full_name", sa.String(128), nullable=False),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("external_ref_id", sa.String(64), nullable=True),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("status", case_status_enum, nullable=False, server_default="open"),
        sa.Column("prequal_verdict", sa.String(32), nullable=True),
        sa.Column("prequal_reasoning", sa.Text(), nullable=True),
        sa.Column("prequal_next_step", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_origination_cases_tenant_id", "origination_cases", ["tenant_id"])
    op.create_index("ix_origination_cases_origination_type", "origination_cases", ["origination_type"])

    op.create_table(
        "origination_decisions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("case_id", sa.CHAR(36), sa.ForeignKey("origination_cases.id"), nullable=False),
        sa.Column("status", org_decision_status_enum, nullable=False, server_default="awaiting_score"),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("score_reasoning", sa.Text(), nullable=True),
        sa.Column("recommended_outcome", recommended_outcome_enum, nullable=True),
        sa.Column("accountable_role", sa.String(128), nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decision_reasoning", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_origination_decisions_tenant_id", "origination_decisions", ["tenant_id"])
    op.create_index("ix_origination_decisions_case_id", "origination_decisions", ["case_id"])

    op.create_table(
        "org_funding_sources",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("applicable_origination_type", origination_type_enum, nullable=False),
        sa.Column("min_risk_score", sa.Float(), nullable=False),
        sa.Column("max_amount_usd", sa.Float(), nullable=False),
        sa.Column("reserve_participation_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_org_funding_sources_tenant_id", "org_funding_sources", ["tenant_id"])
    op.create_index("ix_org_funding_sources_applicable_origination_type", "org_funding_sources", ["applicable_origination_type"])


def downgrade() -> None:
    op.drop_table("org_funding_sources")
    op.drop_table("origination_decisions")
    op.drop_table("origination_cases")
    bind = op.get_bind()
    for enum in (recommended_outcome_enum, org_decision_status_enum, case_status_enum, origination_type_enum):
        enum.drop(bind, checkfirst=True)
