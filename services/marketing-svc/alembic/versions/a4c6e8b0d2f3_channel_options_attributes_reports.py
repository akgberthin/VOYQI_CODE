"""channel options on drafts, contact attributes, scheduled report email

Revision ID: a4c6e8b0d2f3
Revises: f2a6c8e1b3d5
Create Date: 2026-10-29
"""
import sqlalchemy as sa
from alembic import op

revision = "a4c6e8b0d2f3"
down_revision = "f2a6c8e1b3d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("content_drafts") as batch:
        batch.add_column(sa.Column("options", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("segment_members") as batch:
        batch.add_column(sa.Column("attributes", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("marketing_settings") as batch:
        batch.add_column(sa.Column("report_frequency", sa.String(length=16), nullable=False, server_default="off"))
        batch.add_column(sa.Column("report_recipients", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("report_last_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("marketing_settings") as batch:
        batch.drop_column("report_last_sent_at")
        batch.drop_column("report_recipients")
        batch.drop_column("report_frequency")
    with op.batch_alter_table("segment_members") as batch:
        batch.drop_column("attributes")
    with op.batch_alter_table("content_drafts") as batch:
        batch.drop_column("options")
