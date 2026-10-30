"""A/B tests: the share of the audience that gets variant B

Revision ID: a7c9e1b3d5f8
Revises: f5b7d9a1c3e6
Create Date: 2026-10-30
"""
import sqlalchemy as sa
from alembic import op

revision = "a7c9e1b3d5f8"
down_revision = "f5b7d9a1c3e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ab_tests") as batch:
        batch.add_column(sa.Column("split_b_pct", sa.Integer(), nullable=False, server_default="50"))


def downgrade() -> None:
    with op.batch_alter_table("ab_tests") as batch:
        batch.drop_column("split_b_pct")
