"""add security setting category

Revision ID: b3d5f7a9c1e2
Revises: a1c5e9f3b7d2
Create Date: 2026-10-30 11:00:00

The tenant security policy (required MFA, CORS origins) is stored as a
PlatformSetting under this category. PostgreSQL needs ALTER TYPE to add
an enum member; SQLite has no enum type, so it's a no-op there.
"""
import sqlalchemy as sa
from alembic import op

revision = "b3d5f7a9c1e2"
down_revision = "a1c5e9f3b7d2"
branch_labels = None
depends_on = None


CATEGORIES = ("PAYMENT_GATEWAY", "BRANDING", "FEATURE_FLAG", "GENERAL", "AI_CONTENT_ASSIST", "SECURITY")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE settingcategory ADD VALUE IF NOT EXISTS 'SECURITY'")
    else:
        # Non-native enums are a VARCHAR sized to the longest member; it was
        # created at 15 and never widened for AI_CONTENT_ASSIST (17).
        with op.batch_alter_table("platform_settings") as batch_op:
            batch_op.alter_column("category", existing_type=sa.String(15), type_=sa.Enum(*CATEGORIES, name="settingcategory"), existing_nullable=False)


def downgrade() -> None:
    # Removing an enum value needs the type recreated; leave it.
    pass
