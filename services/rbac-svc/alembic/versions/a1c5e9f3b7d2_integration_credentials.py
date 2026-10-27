"""integration credentials (encrypted third-party keys per tenant)

Revision ID: a1c5e9f3b7d2
Revises: 689fb0b32277
Create Date: 2026-10-27
"""
import sqlalchemy as sa
from alembic import op

import src.core.database

revision = "a1c5e9f3b7d2"
down_revision = "689fb0b32277"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_credentials",
        sa.Column("id", src.core.database.GUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("config_encrypted", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("test_status", sa.String(length=16), nullable=False),
        sa.Column("test_detail", sa.Text(), nullable=False),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_integration_tenant_provider"),
    )
    op.create_index(op.f("ix_integration_credentials_tenant_id"), "integration_credentials", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_integration_credentials_tenant_id"), table_name="integration_credentials")
    op.drop_table("integration_credentials")
