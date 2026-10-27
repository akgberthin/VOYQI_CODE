"""social publishing outcome and claim; hosted media assets

Revision ID: c5e8a2b4d6f1
Revises: b3d7f1a9c2e4
Create Date: 2026-10-27
"""
import sqlalchemy as sa
from alembic import op

import src.core.database

revision = "c5e8a2b4d6f1"
down_revision = "b3d7f1a9c2e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", src.core.database.GUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("generation_request_id", src.core.database.GUID(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_media_assets_tenant_id"), "media_assets", ["tenant_id"], unique=False)
    with op.batch_alter_table("social_posts") as batch:
        batch.add_column(sa.Column("delivery_mode", sa.String(length=16), nullable=False, server_default=""))
        batch.add_column(sa.Column("error_detail", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("media_asset_id", src.core.database.GUID(), nullable=True))
        batch.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE social_posts SET claimed_at = published_at, delivery_mode = 'external' WHERE published_at IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table("social_posts") as batch:
        batch.drop_column("claimed_at")
        batch.drop_column("media_asset_id")
        batch.drop_column("error_detail")
        batch.drop_column("delivery_mode")
    op.drop_index(op.f("ix_media_assets_tenant_id"), table_name="media_assets")
    op.drop_table("media_assets")
