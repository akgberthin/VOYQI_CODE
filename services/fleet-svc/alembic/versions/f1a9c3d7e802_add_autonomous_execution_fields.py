"""add autonomous execution fields

Revision ID: f1a9c3d7e802
Revises: a7dd087e6a37
Create Date: 2026-08-10 00:00:00.000000

Adds the fields needed for the Fleet Intelligence Agent to execute
terminal actions directly rather than writing advisory-only
recommendations: Vehicle.held_for_review / held_for_review_reason
(FLT-001), Vehicle.flagged_for_investigation (FLT-002), and
FloorplanFacility.frozen / frozen_reason (FLT-008/009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a9c3d7e802'
down_revision = 'a7dd087e6a37'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('vehicles', sa.Column('held_for_review', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('vehicles', sa.Column('held_for_review_reason', sa.Text(), nullable=False, server_default=''))
    op.add_column('vehicles', sa.Column('flagged_for_investigation', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('floorplan_facilities', sa.Column('frozen', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('floorplan_facilities', sa.Column('frozen_reason', sa.Text(), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('floorplan_facilities', 'frozen_reason')
    op.drop_column('floorplan_facilities', 'frozen')
    op.drop_column('vehicles', 'flagged_for_investigation')
    op.drop_column('vehicles', 'held_for_review_reason')
    op.drop_column('vehicles', 'held_for_review')
