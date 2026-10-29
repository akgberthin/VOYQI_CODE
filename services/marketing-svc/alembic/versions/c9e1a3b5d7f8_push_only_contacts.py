"""contacts reachable only by push: push_token joins the contact uniqueness

Revision ID: c9e1a3b5d7f8
Revises: b7d9f1a3c5e6
Create Date: 2026-10-29
"""
from alembic import op

revision = "c9e1a3b5d7f8"
down_revision = "b7d9f1a3c5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("segment_members") as batch:
        batch.drop_constraint("uq_segment_member_contact", type_="unique")
        batch.create_unique_constraint("uq_segment_member_contact", ["segment_id", "email", "phone", "push_token"])


def downgrade() -> None:
    with op.batch_alter_table("segment_members") as batch:
        batch.drop_constraint("uq_segment_member_contact", type_="unique")
        batch.create_unique_constraint("uq_segment_member_contact", ["segment_id", "email", "phone"])
