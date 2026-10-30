"""named reports emailed on a schedule, as CSV or PDF

Revision ID: e3a5c7e9b1d4
Revises: d1f3a5c7e9b2
Create Date: 2026-10-30
"""
import sqlalchemy as sa
from alembic import op

import src.core.database

revision = "e3a5c7e9b1d4"
down_revision = "d1f3a5c7e9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_schedules",
        sa.Column("id", src.core.database.GUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_schedules_tenant_id", "report_schedules", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_report_schedules_tenant_id", "report_schedules")
    op.drop_table("report_schedules")
