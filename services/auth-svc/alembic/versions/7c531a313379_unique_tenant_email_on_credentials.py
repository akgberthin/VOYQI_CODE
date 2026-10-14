"""unique tenant email on credentials

Revision ID: 7c531a313379
Revises: 4387413d2fe7
Create Date: 2026-10-15 04:55:43.524682

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '7c531a313379'
down_revision = '4387413d2fe7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch mode so the same migration runs on SQLite (no ALTER ... ADD
    # CONSTRAINT) and PostgreSQL. Fails if duplicate (tenant_id, email)
    # rows already exist — resolve those first; they break login.
    with op.batch_alter_table("user_credentials") as batch_op:
        batch_op.create_unique_constraint("uq_user_credentials_tenant_email", ["tenant_id", "email"])


def downgrade() -> None:
    with op.batch_alter_table("user_credentials") as batch_op:
        batch_op.drop_constraint("uq_user_credentials_tenant_email", type_="unique")
