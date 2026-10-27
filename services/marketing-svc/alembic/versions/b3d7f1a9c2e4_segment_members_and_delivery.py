"""segment members; delivery outcome and scheduler claim on sends

Revision ID: b3d7f1a9c2e4
Revises: 14e82fbfd5f5
Create Date: 2026-10-27
"""
import sqlalchemy as sa
from alembic import op

import src.core.database

revision = "b3d7f1a9c2e4"
down_revision = "14e82fbfd5f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "segment_members",
        sa.Column("id", src.core.database.GUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("segment_id", src.core.database.GUID(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("push_token", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["segment_id"], ["audience_segments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_id", "email", "phone", name="uq_segment_member_contact"),
    )
    op.create_index(op.f("ix_segment_members_tenant_id"), "segment_members", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_segment_members_segment_id"), "segment_members", ["segment_id"], unique=False)
    with op.batch_alter_table("scheduled_sends") as batch:
        batch.add_column(sa.Column("recipients_failed", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("recipients_console", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("failure_detail", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    # Sends executed before this migration count as claimed.
    op.execute("UPDATE scheduled_sends SET claimed_at = sent_at WHERE sent_at IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table("scheduled_sends") as batch:
        batch.drop_column("claimed_at")
        batch.drop_column("failure_detail")
        batch.drop_column("recipients_console")
        batch.drop_column("recipients_failed")
    op.drop_index(op.f("ix_segment_members_segment_id"), table_name="segment_members")
    op.drop_index(op.f("ix_segment_members_tenant_id"), table_name="segment_members")
    op.drop_table("segment_members")
