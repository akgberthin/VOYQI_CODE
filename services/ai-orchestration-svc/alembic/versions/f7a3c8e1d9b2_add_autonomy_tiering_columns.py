"""add autonomy tiering columns

Revision ID: f7a3c8e1d9b2
Revises: 2d00749b98b2
Create Date: 2026-09-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "f7a3c8e1d9b2"
down_revision = "2d00749b98b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decision_logs", sa.Column("tier", sa.Integer(), nullable=True))
    op.add_column(
        "decision_logs",
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.add_column("ai_recommendations", sa.Column("tier", sa.Integer(), nullable=True))
    op.add_column(
        "ai_recommendations",
        sa.Column("requires_accountable_role", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("ai_recommendations", sa.Column("accountable_role", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_recommendations", "accountable_role")
    op.drop_column("ai_recommendations", "requires_accountable_role")
    op.drop_column("ai_recommendations", "tier")

    op.drop_column("decision_logs", "approval_required")
    op.drop_column("decision_logs", "tier")
