"""add ai_content_assist setting category

Revision ID: 689fb0b32277
Revises: f893351f6831
Create Date: 2026-08-10 18:45:00.000000

Adds AI_CONTENT_ASSIST to the settingcategory enum — the category the
AI Assist Content Settings page writes its config under (brand voice,
default model, auto-generate behavior, review-required flag). Reuses
PlatformSetting rather than a new table, since this is genuinely the
same shape of thing as a payment gateway or feature flag setting.

PostgreSQL enums require ALTER TYPE ... ADD VALUE to add a member;
SQLite has no native enum type, so this is a no-op there.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '689fb0b32277'
down_revision = 'f893351f6831'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TYPE settingcategory ADD VALUE IF NOT EXISTS 'AI_CONTENT_ASSIST'")


def downgrade() -> None:
    # PostgreSQL does not support removing a value from an existing
    # enum type without recreating it; no-op is the safe default
    # rather than risking data loss on any row already using it.
    pass
