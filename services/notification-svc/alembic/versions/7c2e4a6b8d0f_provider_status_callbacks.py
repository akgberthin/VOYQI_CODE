"""what the provider reported after sending (Twilio status callbacks)

Revision ID: 7c2e4a6b8d0f
Revises: 136ea3f92b2c
Create Date: 2026-10-30
"""
import sqlalchemy as sa
from alembic import op

revision = "7c2e4a6b8d0f"
down_revision = "136ea3f92b2c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("provider_status", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("answered_by", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("duration_seconds", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("recording_url", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        for col in ("status_updated_at", "recording_url", "duration_seconds", "answered_by", "provider_status"):
            batch.drop_column(col)
