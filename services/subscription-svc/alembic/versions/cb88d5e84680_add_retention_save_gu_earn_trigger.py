"""add retention_save gu earn trigger

Revision ID: cb88d5e84680
Revises: 5efef43dfd7f
Create Date: 2026-08-11 00:00:00.000000

Adds RETENTION_SAVE to the guearntrigger enum — awarded autonomously by
the Member Experience Agent when a member's churn-risk score crosses
the proactive-intervention threshold. This is a distinct trigger from
the winback flow, which only ever fires once a member has already
initiated a cancellation and is a member-facing offer, not an
autonomous internal action.

PostgreSQL enums require ALTER TYPE ... ADD VALUE to add a member;
SQLite has no native enum type (SQLAlchemy validates enum membership
at the Python layer only), so this is a no-op there — the column
already accepts any string value the application considers valid.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'cb88d5e84680'
down_revision = '5efef43dfd7f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE guearntrigger ADD VALUE IF NOT EXISTS 'RETENTION_SAVE'")


def downgrade() -> None:
    # PostgreSQL does not support removing a value from an existing
    # enum type without recreating it; a real rollback would need a
    # full type-recreation migration if this value is ever in use.
    # No-op is the correct, safe default here rather than risking data
    # loss on any row that has already used this trigger.
    pass
