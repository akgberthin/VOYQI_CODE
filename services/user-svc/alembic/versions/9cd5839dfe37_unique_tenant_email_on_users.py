"""unique tenant email on users

Revision ID: 9cd5839dfe37
Revises: 23f777faa6d2
Create Date: 2026-10-15 04:55:50.612001

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '9cd5839dfe37'
down_revision = '23f777faa6d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch mode so the same migration runs on SQLite (no ALTER ... ADD
    # CONSTRAINT) and PostgreSQL. Fails if duplicate (tenant_id, email)
    # rows already exist — resolve those first; they break login.
    with op.batch_alter_table("users") as batch_op:
        batch_op.create_unique_constraint("uq_users_tenant_email", ["tenant_id", "email"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_tenant_email", type_="unique")
