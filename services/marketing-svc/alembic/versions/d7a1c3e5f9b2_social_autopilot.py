"""social autopilot: per-tenant settings and planned slots

Revision ID: d7a1c3e5f9b2
Revises: c5e8a2b4d6f1
Create Date: 2026-10-27
"""
import sqlalchemy as sa
from alembic import op

import src.core.database

revision = "d7a1c3e5f9b2"
down_revision = "c5e8a2b4d6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autopilot_configs",
        sa.Column("id", src.core.database.GUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("account_ids", sa.JSON(), nullable=False),
        sa.Column("posts_per_week", sa.Integer(), nullable=False),
        sa.Column("post_hour_utc", sa.Integer(), nullable=False),
        sa.Column("themes", sa.Text(), nullable=False),
        sa.Column("mobility_class", sa.String(length=32), nullable=False),
        sa.Column("tone", sa.String(length=16), nullable=False),
        sa.Column("include_images", sa.Boolean(), nullable=False),
        sa.Column("last_planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id"),
    )
    op.create_table(
        "autopilot_slots",
        sa.Column("id", src.core.database.GUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("social_account_id", src.core.database.GUID(), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("theme", sa.String(length=512), nullable=False),
        sa.Column("content_draft_id", src.core.database.GUID(), nullable=True),
        sa.Column("media_asset_id", src.core.database.GUID(), nullable=True),
        sa.Column("social_post_id", src.core.database.GUID(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["social_account_id"], ["social_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_autopilot_slots_tenant_id"), "autopilot_slots", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_autopilot_slots_tenant_id"), table_name="autopilot_slots")
    op.drop_table("autopilot_slots")
    op.drop_table("autopilot_configs")
