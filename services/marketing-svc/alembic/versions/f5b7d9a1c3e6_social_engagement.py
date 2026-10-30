"""engagement read back from the social platforms

Revision ID: f5b7d9a1c3e6
Revises: e3a5c7e9b1d4
Create Date: 2026-10-30
"""
import sqlalchemy as sa
from alembic import op

revision = "f5b7d9a1c3e6"
down_revision = "e3a5c7e9b1d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("social_posts") as batch:
        for col in ("likes", "comments", "shares", "impressions"):
            batch.add_column(sa.Column(col, sa.Integer(), nullable=True))
        batch.add_column(sa.Column("metrics_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("metrics_error", sa.String(length=512), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("social_posts") as batch:
        for col in ("metrics_error", "metrics_updated_at", "impressions", "shares", "comments", "likes"):
            batch.drop_column(col)
