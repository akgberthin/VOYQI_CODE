"""pending MFA secret until the new authenticator is confirmed

Revision ID: 9b4e1d7a2c60
Revises: 7c531a313379
Create Date: 2026-10-30 10:00:00

"""
import sqlalchemy as sa
from alembic import op

revision = "9b4e1d7a2c60"
down_revision = "7c531a313379"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_credentials", sa.Column("mfa_pending_secret", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_credentials") as batch_op:
        batch_op.drop_column("mfa_pending_secret")
