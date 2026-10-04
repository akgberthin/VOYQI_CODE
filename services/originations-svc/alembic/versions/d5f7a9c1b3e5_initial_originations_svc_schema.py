"""initial originations_svc schema

Revision ID: d5f7a9c1b3e5
Revises:
Create Date: 2026-09-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "d5f7a9c1b3e5"
down_revision = None
branch_labels = None
depends_on = None

app_status_enum = sa.Enum("draft", "scored", "pending_decision", "approved", "declined", name="applicationstatus")
lead_band_enum = sa.Enum("hot", "warm", "cold", name="leadband")
decision_status_enum = sa.Enum("pending", "approved", "declined", name="creditdecisionstatus")


def upgrade() -> None:
    op.create_table(
        "loan_applications",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("applicant_name", sa.String(128), nullable=False),
        sa.Column("requested_amount_usd", sa.Float(), nullable=False),
        sa.Column("annual_income_usd", sa.Float(), nullable=False),
        sa.Column("fico_score", sa.Integer(), nullable=False),
        sa.Column("status", app_status_enum, nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_loan_applications_tenant_id", "loan_applications", ["tenant_id"])

    op.create_table(
        "application_scores",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("application_id", sa.CHAR(36), sa.ForeignKey("loan_applications.id"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("band", lead_band_enum, nullable=False),
        sa.Column("drivers", sa.JSON(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_application_scores_tenant_id", "application_scores", ["tenant_id"])
    op.create_index("ix_application_scores_application_id", "application_scores", ["application_id"])

    op.create_table(
        "credit_decisions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("application_id", sa.CHAR(36), sa.ForeignKey("loan_applications.id"), nullable=False),
        sa.Column("status", decision_status_enum, nullable=False, server_default="pending"),
        sa.Column("accountable_role", sa.String(128), nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decision_reasoning", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_credit_decisions_tenant_id", "credit_decisions", ["tenant_id"])
    op.create_index("ix_credit_decisions_application_id", "credit_decisions", ["application_id"])


def downgrade() -> None:
    op.drop_table("credit_decisions")
    op.drop_table("application_scores")
    op.drop_table("loan_applications")
    app_status_enum.drop(op.get_bind(), checkfirst=True)
    lead_band_enum.drop(op.get_bind(), checkfirst=True)
    decision_status_enum.drop(op.get_bind(), checkfirst=True)
