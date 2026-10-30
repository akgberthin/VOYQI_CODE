"""provider outcomes on each message (delivered, answered…) and voice retries

Revision ID: d1f3a5c7e9b2
Revises: c9e1a3b5d7f8
Create Date: 2026-10-30
"""
import sqlalchemy as sa
from alembic import op

import src.core.database

revision = "d1f3a5c7e9b2"
down_revision = "c9e1a3b5d7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("message_recipients") as batch:
        batch.add_column(sa.Column("notification_id", src.core.database.GUID(), nullable=True))
        batch.add_column(sa.Column("outcome", sa.String(length=32), nullable=False, server_default=""))
        batch.add_column(sa.Column("recording_url", sa.String(length=1024), nullable=False, server_default=""))
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("retry_payload", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("message_recipients") as batch:
        for col in ("retry_payload", "next_attempt_at", "attempts", "recording_url", "outcome", "notification_id"):
            batch.drop_column(col)
