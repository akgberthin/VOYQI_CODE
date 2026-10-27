"""automation journeys, channel settings (voice window), voice consent,
recurrence on sends and posts

Revision ID: e9b3d5f7a1c4
Revises: d7a1c3e5f9b2
Create Date: 2026-10-27
"""
import sqlalchemy as sa
from alembic import op

import src.core.database

revision = "e9b3d5f7a1c4"
down_revision = "d7a1c3e5f9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketing_settings",
        sa.Column("id", src.core.database.GUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("voice_window_start", sa.Integer(), nullable=False),
        sa.Column("voice_window_end", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id"),
    )
    op.create_table(
        "journeys",
        sa.Column("id", src.core.database.GUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("trigger_event", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_journeys_tenant_id"), "journeys", ["tenant_id"], unique=False)
    op.create_table(
        "journey_enrollments",
        sa.Column("id", src.core.database.GUID(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("journey_id", src.core.database.GUID(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("push_token", sa.String(length=512), nullable=False),
        sa.Column("consent_voice", sa.Boolean(), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["journey_id"], ["journeys.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_journey_enrollments_tenant_id"), "journey_enrollments", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_journey_enrollments_journey_id"), "journey_enrollments", ["journey_id"], unique=False)
    with op.batch_alter_table("segment_members") as batch:
        batch.add_column(sa.Column("consent_voice", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("scheduled_sends") as batch:
        batch.add_column(sa.Column("recurrence", sa.String(length=16), nullable=False, server_default="none"))
    with op.batch_alter_table("social_posts") as batch:
        batch.add_column(sa.Column("recurrence", sa.String(length=16), nullable=False, server_default="none"))


def downgrade() -> None:
    with op.batch_alter_table("social_posts") as batch:
        batch.drop_column("recurrence")
    with op.batch_alter_table("scheduled_sends") as batch:
        batch.drop_column("recurrence")
    with op.batch_alter_table("segment_members") as batch:
        batch.drop_column("consent_voice")
    op.drop_index(op.f("ix_journey_enrollments_journey_id"), table_name="journey_enrollments")
    op.drop_index(op.f("ix_journey_enrollments_tenant_id"), table_name="journey_enrollments")
    op.drop_table("journey_enrollments")
    op.drop_index(op.f("ix_journeys_tenant_id"), table_name="journeys")
    op.drop_table("journeys")
    op.drop_table("marketing_settings")
