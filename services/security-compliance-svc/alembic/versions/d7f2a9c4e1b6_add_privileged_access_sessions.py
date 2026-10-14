"""add privileged access sessions (break-glass, impersonation)

Revision ID: d7f2a9c4e1b6
Revises: c4e6a8b1d3f5
Create Date: 2026-10-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "d7f2a9c4e1b6"
down_revision = "c4e6a8b1d3f5"
branch_labels = None
depends_on = None

session_status_enum = sa.Enum("active", "ended", name="accesssessionstatus")


def upgrade() -> None:
    op.create_table(
        "break_glass_sessions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("activated_by", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("off_hours", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", session_status_enum, nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by", sa.String(128), nullable=True),
        sa.Column("end_reason", sa.String(512), nullable=True),
    )
    op.create_index("ix_break_glass_sessions_tenant_id", "break_glass_sessions", ["tenant_id"])
    op.create_index("ix_break_glass_sessions_user_id", "break_glass_sessions", ["user_id"])

    op.create_table(
        "impersonation_sessions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("admin_user_id", sa.String(64), nullable=False),
        sa.Column("target_user_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("session_recording_ref", sa.String(256), nullable=True),
        sa.Column("off_hours", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", session_status_enum, nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by", sa.String(128), nullable=True),
        sa.Column("end_reason", sa.String(512), nullable=True),
    )
    op.create_index("ix_impersonation_sessions_tenant_id", "impersonation_sessions", ["tenant_id"])
    op.create_index("ix_impersonation_sessions_admin_user_id", "impersonation_sessions", ["admin_user_id"])
    op.create_index("ix_impersonation_sessions_target_user_id", "impersonation_sessions", ["target_user_id"])


def downgrade() -> None:
    op.drop_table("impersonation_sessions")
    op.drop_table("break_glass_sessions")
    session_status_enum.drop(op.get_bind(), checkfirst=True)
