"""per-recipient message tracking (opens, clicks, A/B arm), SMS quiet hours

Revision ID: b7d9f1a3c5e6
Revises: a4c6e8b0d2f3
Create Date: 2026-10-29
"""
import sqlalchemy as sa
from alembic import op

import src.core.database

revision = "b7d9f1a3c5e6"
down_revision = "a4c6e8b0d2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_recipients",
        sa.Column("id", src.core.database.GUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("send_id", src.core.database.GUID(), nullable=False),
        sa.Column("campaign_id", src.core.database.GUID(), nullable=False),
        sa.Column("content_draft_id", src.core.database.GUID(), nullable=True),
        sa.Column("ab_test_id", src.core.database.GUID(), nullable=True),
        sa.Column("variant", sa.String(length=1), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("contact", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("delivery_mode", sa.String(length=32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opens", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_message_recipients_tenant_id", "message_recipients", ["tenant_id"])
    op.create_index("ix_message_recipients_send_id", "message_recipients", ["send_id"])
    op.create_index("ix_message_recipients_campaign_id", "message_recipients", ["campaign_id"])
    with op.batch_alter_table("marketing_settings") as batch:
        batch.add_column(sa.Column("sms_window_start", sa.Integer(), nullable=False, server_default="9"))
        batch.add_column(sa.Column("sms_window_end", sa.Integer(), nullable=False, server_default="21"))


def downgrade() -> None:
    with op.batch_alter_table("marketing_settings") as batch:
        batch.drop_column("sms_window_end")
        batch.drop_column("sms_window_start")
    op.drop_index("ix_message_recipients_campaign_id", "message_recipients")
    op.drop_index("ix_message_recipients_send_id", "message_recipients")
    op.drop_index("ix_message_recipients_tenant_id", "message_recipients")
    op.drop_table("message_recipients")
